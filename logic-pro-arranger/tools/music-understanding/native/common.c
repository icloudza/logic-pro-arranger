#define _XOPEN_SOURCE 700
#include "music.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#ifdef __APPLE__
#define COMMON_DIGEST_FOR_OPENSSL
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/evp.h>
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

sqlite3 *music_db = NULL;
char music_home[4096];

/* Resolve existing components and symlinks before creating an output directory. */
void normalize_path(const char *path, char out[4096]) {
    char input[4096], current[4096], candidate[4096];
    const char *home = getenv("HOME");
    int size;
    if (path[0] == '~' && (path[1] == '/' || !path[1])) {
        if (!home) fail("HOME 未设置");
        size = snprintf(input, sizeof(input), "%s%s", home, path + 1);
    } else size = snprintf(input, sizeof(input), "%s", path);
    if (size < 0 || size >= (int)sizeof(input) || !*input) fail("缓存路径无效");
    if (input[0] == '/') strcpy(current, "/");
    else if (!getcwd(current, sizeof(current))) fail("无法读取当前目录");
    char *context = NULL;
    for (char *part = strtok_r(input, "/", &context); part; part = strtok_r(NULL, "/", &context)) {
        if (!strcmp(part, ".")) continue;
        if (!strcmp(part, "..")) {
            char *last = strrchr(current, '/');
            if (last && last != current) *last = 0; else strcpy(current, "/");
            continue;
        }
        size = snprintf(candidate, sizeof(candidate), "%s%s%s", current, !strcmp(current,"/") ? "" : "/", part);
        if (size < 0 || size >= (int)sizeof(candidate)) fail("路径过长");
        if (!realpath(candidate, current)) {
            if (errno != ENOENT) fail("无法解析缓存目录");
            strcpy(current, candidate);
        }
    }
    strcpy(out, current);
}

void fail(const char *message) {
    cJSON *r = cJSON_CreateObject(); cJSON_AddStringToObject(r, "error", message);
    emit(r); if (music_db) sqlite3_close(music_db); exit(1);
}
void emit(cJSON *value) {
    if (!value) { fputs("{\"error\":\"out of memory\"}\n", stdout); exit(1); }
    char *s = cJSON_PrintUnformatted(value);
    if (!s) { cJSON_Delete(value); exit(1); }
    puts(s); free(s); cJSON_Delete(value);
}
void makedirs(const char *path) {
    char p[4096];
    if (strlen(path) >= sizeof(p)) fail("路径过长");
    strcpy(p, path);
    for (char *s = p + 1; ; s++) {
        if (*s == '/' || !*s) {
            char c = *s; *s = 0;
            if (mkdir(p, 0755) && errno != EEXIST) fail("无法创建缓存目录");
            *s = c; if (!c) break;
        }
    }
}
char *capture(char *const argv[]) {
    int fd[2]; if (pipe(fd)) fail("pipe 失败");
    pid_t pid = fork(); if (pid < 0) fail("fork 失败");
    if (!pid) {
        close(fd[0]); dup2(fd[1], STDOUT_FILENO); close(fd[1]);
        char key[128], candidate[4096];
        snprintf(key, sizeof(key), "MUSIC_%s", argv[0]);
        for (char *p = key; *p; p++) if (*p >= 'a' && *p <= 'z') *p -= 'a'-'A';
        const char *explicit = getenv(key);
        if (explicit) { execv(explicit, argv); _exit(127); }
        execvp(argv[0], argv);
        const char *dirs[] = {"/opt/homebrew/bin", "/usr/local/bin"};
        for (int i=0; i<2; i++) {
            snprintf(candidate, sizeof(candidate), "%s/%s", dirs[i], argv[0]);
            execv(candidate, argv);
        }
        _exit(127);
    }
    close(fd[1]); size_t used = 0, cap = 8192; char *buf = malloc(cap);
    if (!buf) fail("内存不足");
    for (;;) {
        if (used + 4096 + 1 > cap) {
            cap *= 2; if (cap > 64 * 1024 * 1024) fail("子进程输出超过上限");
            char *next = realloc(buf, cap); if (!next) fail("内存不足"); buf = next;
        }
        ssize_t n = read(fd[0], buf + used, cap - used - 1);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) fail("读取子进程失败");
        if (!n) break;
        used += (size_t)n;
    }
    close(fd[0]); int status;
    while (waitpid(pid, &status, 0) < 0) if (errno != EINTR) fail("waitpid 失败");
    if (!WIFEXITED(status) || WEXITSTATUS(status)) { free(buf); fail("外部程序失败，请检查 stderr"); }
    buf[used] = 0; return buf;
}
#ifdef __APPLE__
static void hash_stream(FILE *f, const void *bytes, size_t length, char hex[65]) {
    CC_SHA256_CTX ctx;
    unsigned char digest[CC_SHA256_DIGEST_LENGTH], buffer[65536];
    CC_SHA256_Init(&ctx);
    if (f) {
        size_t n;
        while ((n = fread(buffer, 1, sizeof(buffer), f))) CC_SHA256_Update(&ctx, buffer, (CC_LONG)n);
        if (ferror(f)) fail("读取源文件失败");
    } else {
        const unsigned char *p = bytes;
        while (length) {
            size_t n = length > sizeof(buffer) ? sizeof(buffer) : length;
            CC_SHA256_Update(&ctx, p, (CC_LONG)n); p += n; length -= n;
        }
    }
    CC_SHA256_Final(digest, &ctx);
    for (unsigned i = 0; i < sizeof(digest); i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
}
void hash_file(const char *path, char hex[65]) {
    FILE *f = fopen(path, "rb"); if (!f) fail("无法读取源文件");
    hash_stream(f, NULL, 0, hex); fclose(f);
}
void hash_bytes(const void *data, size_t n, char hex[65]) { hash_stream(NULL, data, n, hex); }
#else
static void finish_hash(EVP_MD_CTX *ctx, char hex[65]) {
    unsigned char out[EVP_MAX_MD_SIZE]; unsigned len = 0;
    if (!EVP_DigestFinal_ex(ctx, out, &len) || len != 32) fail("SHA256 失败");
    EVP_MD_CTX_free(ctx);
    for (unsigned i = 0; i < len; i++) snprintf(hex + i * 2, 3, "%02x", out[i]);
}
void hash_file(const char *path, char hex[65]) {
    FILE *f = fopen(path, "rb"); if (!f) fail("无法读取源文件");
    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    if (!ctx || !EVP_DigestInit_ex(ctx, EVP_sha256(), NULL)) fail("SHA256 初始化失败");
    unsigned char data[65536]; size_t n;
    while ((n = fread(data, 1, sizeof(data), f))) if (!EVP_DigestUpdate(ctx, data, n)) fail("SHA256 失败");
    if (ferror(f)) fail("读取源文件失败"); fclose(f); finish_hash(ctx, hex);
}
void hash_bytes(const void *data, size_t n, char hex[65]) {
    EVP_MD_CTX *ctx = EVP_MD_CTX_new();
    if (!ctx || !EVP_DigestInit_ex(ctx, EVP_sha256(), NULL) || !EVP_DigestUpdate(ctx, data, n)) fail("SHA256 失败");
    finish_hash(ctx, hex);
}
#endif
const char *option(int argc, char **argv, const char *key, const char *fallback) {
    for (int i = 1; i < argc; i++) if (!strcmp(argv[i], key)) {
        if (i + 1 >= argc) fail("选项缺少参数"); return argv[i + 1];
    }
    return fallback;
}
