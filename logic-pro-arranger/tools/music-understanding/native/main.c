#define _XOPEN_SOURCE 700
#include "music.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif

static double numeric(const char *s) {
    char *end = NULL; errno = 0; double v = strtod(s, &end);
    if (errno || end == s || *end || !isfinite(v)) fail("无效数字参数"); return v;
}
static int integer(const char *s, int low, int high) {
    double n = numeric(s);
    if (n < low || n > high || floor(n) != n) fail("整数选项超出范围");
    return (int)n;
}
static void validate_options(int argc, char **argv, int at, const char *cmd) {
    const char *query[]={"--from","--to","--part","--stem","--type","--id","--limit","--offset","--task",NULL};
    const char *search[]={"--text","--limit",NULL};
    const char *index[]={"--limit",NULL};
    const char *empty[]={NULL};
    const char **allowed=!strcmp(cmd,"query")?query:!strcmp(cmd,"search")?search:!strcmp(cmd,"index")?index:empty;
    for(int i=at+1;i<argc;i++) if(!strncmp(argv[i],"--",2)) {
        int known=0; for(int k=0;allowed[k];k++) if(!strcmp(argv[i],allowed[k])) known=1;
        if(!known) fail("未知选项；公共 --home 应放在子命令前");
        if(++i>=argc) fail("选项缺少参数");
    }
}
static void worker(int argc, char **argv) {
    const char *python = getenv("MUSIC_PYTHON"); char local[4096], exe[4096], root[4096]; int found = 0;
    const char *home = getenv("HOME"); if (!home) home = ".";
    if (!python) {
        snprintf(local, sizeof(local), "%s/.cache/music-understanding/runtime-py311/bin/python", home);
        python = local;
    }
    if (access(python, X_OK) != 0) fail("缺少 Python 运行环境：运行 tools/music-understanding/scripts/bootstrap.sh core（模型用 models），或设置 MUSIC_PYTHON 为完整路径");
#ifdef __APPLE__
    uint32_t size = sizeof(exe); found = _NSGetExecutablePath(exe, &size) == 0;
#elif defined(__linux__)
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe)-1);
    if (n > 0) { exe[n] = 0; found = 1; }
#endif
    if (found && realpath(exe, root)) {
        char *slash = strrchr(root, '/'); if (slash) *slash = 0;
        slash = strrchr(root, '/'); if (slash) *slash = 0;
        setenv("MUSIC_ROOT", root, 1);
    }
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    char cache[4096]; snprintf(cache, sizeof(cache), "%s/.cache/music-understanding/numba", home);
    setenv("NUMBA_CACHE_DIR", cache, 0);
    char **args = calloc((size_t)argc + 3, sizeof(char *)); if (!args) fail("内存不足");
    args[0] = (char *)python; args[1] = "-m"; args[2] = "music_understanding";
    for (int i = 1; i < argc; i++) args[i + 2] = argv[i];
    if (music_db) { sqlite3_close(music_db); music_db = NULL; }
    execvp(python, args); free(args); fail("模型 worker 不可用。安装 Python 工具包或设置 MUSIC_PYTHON。");
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--version")) { puts("0.2.0"); return 0; }
    int at = 1;
    if (argc > 2 && !strcmp(argv[1], "--home")) at = 3;
    if (argc <= at || !strcmp(argv[at], "--help")) {
        puts("music — C17 音频核心 + Python 模型进程\n原生：index / inspect / query / search --text / dsp / hash\n模型与关系：analyze / compare / render / audio / export / correct / doctor\n公共选项：--home CACHE（放在命令前）\n分析帮助：music analyze --help\nC DSP：music dsp decoded.wav [--pitch] [--fmin 32] [--fmax 2093]"); return 0;
    }
    const char *cmd = argv[at];
    if (!strcmp(cmd, "hash")) { if (argc <= at + 1) fail("缺少文件"); char hash[65]; hash_file(argv[at + 1], hash);
        cJSON *o=cJSON_CreateObject(); cJSON_AddStringToObject(o,"sha256",hash); emit(o); return 0; }
    if (!strcmp(cmd, "dsp")) { if (argc <= at + 1) fail("缺少 WAV 文件"); int pitch=0;
        for(int i=at+2;i<argc;i++) if(!strcmp(argv[i],"--pitch")) pitch=1;
        emit(dsp_file(argv[at+1],numeric(option(argc,argv,"--fmin","32")),numeric(option(argc,argv,"--fmax","2093")),pitch)); return 0; }
    if (strcmp(cmd,"query") && strcmp(cmd,"search") && strcmp(cmd,"index") && strcmp(cmd,"inspect")) worker(argc,argv);
    for(int i=at+1;i<argc;i++) if(!strcmp(argv[i],"--help")) worker(argc,argv);
    if ((!strcmp(cmd,"search") && (option(argc,argv,"--similar",NULL) || option(argc,argv,"--semantic",NULL))) || (!strcmp(cmd,"index") && option(argc,argv,"--tasks",NULL))) worker(argc,argv);
    validate_options(argc,argv,at,cmd);
    const char *home = option(argc,argv,"--home",getenv("MUSIC_HOME"));
    if (home) normalize_path(home,music_home);
    else snprintf(music_home,sizeof(music_home),"%s/.cache/music-understanding/data",getenv("HOME")?getenv("HOME"):".");
    if ((!strcmp(cmd,"index") || !strcmp(cmd,"inspect")) && argc>at+1) {
        char root[4096]; if(!realpath(argv[at+1],root)) fail("源路径不存在");
        if(!strcmp(cmd,"inspect")) { char *last=strrchr(root,'/'); if(last!=root) *last=0; else root[1]=0; }
        size_t n=strlen(root); if(!strcmp(root,"/")||(!strncmp(root,music_home,n)&&(music_home[n]=='/'||!music_home[n]))) fail("缓存目录必须在采样库外");
    }
    open_catalog();
    cJSON *result=NULL; char id[65];
    if (!strcmp(cmd,"search")) {
        int limit=integer(option(argc,argv,"--limit","12"),0,1000);
        result=search_paths(option(argc,argv,"--text",""),limit);
    } else {
        if(argc<=at+1) fail("缺少素材或目录");
        if(!strcmp(cmd,"index")) result=index_directory(argv[at+1],integer(option(argc,argv,"--limit","-1"),-1,2147483647));
        else {
            resolve_asset(argv[at+1],id);
            if(!strcmp(cmd,"inspect")) result=asset_json(id);
            else {
                if(argc<=at+2) fail("query 需要 summary/events/evidence/runs");
                const char *view=argv[at+2];
                if(!strcmp(view,"summary")) result=query_summary(id);
                else if(!strcmp(view,"runs")) result=query_runs(id);
                else if(!strcmp(view,"evidence")) result=query_evidence(id,option(argc,argv,"--id",NULL));
                else if(!strcmp(view,"events")) {
                    int limit=integer(option(argc,argv,"--limit","50"),1,1000),offset=integer(option(argc,argv,"--offset","0"),0,2147482647);
                    double start=numeric(option(argc,argv,"--from","0")),end=numeric(option(argc,argv,"--to","1e100"));
                    if(limit<1||limit>1000||offset<0||start<0||end<=start) fail("无效查询范围");
                    result=query_events(id,start,end,option(argc,argv,"--part",option(argc,argv,"--stem",NULL)),option(argc,argv,"--type",NULL),limit,offset,option(argc,argv,"--task",NULL));
                } else fail("未知 query 类型");
            }
        }
    }
    emit(result); sqlite3_close(music_db); return 0;
}
