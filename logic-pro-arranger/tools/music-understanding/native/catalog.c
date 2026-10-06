#include "schema.h"
#define _XOPEN_SOURCE 700
#include "music.h"
#include <ctype.h>
#include <ftw.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdint.h>

static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static cJSON *embedded_tags(const char *path) {
    cJSON *o = cJSON_CreateObject(); FILE *f = fopen(path, "rb");
    if (!f) return o;
    unsigned char h[36];
    if (fread(h,1,12,f)!=12 || memcmp(h,"RIFF",4) || memcmp(h+8,"WAVE",4)) { fclose(f); return o; }
    struct stat st; if(fstat(fileno(f),&st)) { fclose(f); return o; }
    while (fread(h,1,8,f)==8) {
        uint32_t n=le32(h+4); off_t at=ftello(f);
        if(at<0 || (uint64_t)at+n>(uint64_t)st.st_size) break;
        if(!memcmp(h,"smpl",4) && n>=36 && fread(h,1,36,f)==36) {
            cJSON_Delete(o); o=cJSON_CreateObject();
            cJSON_AddNumberToObject(o,"root_midi",le32(h+12));
            cJSON_AddNumberToObject(o,"pitch_fraction",le32(h+16));
            cJSON_AddNumberToObject(o,"loop_count",le32(h+28));
            cJSON_AddStringToObject(o,"evidence","file_metadata_unverified");
        }
        if(fseeko(f,at+(off_t)n+(n%2),SEEK_SET)) break;
    }
    fclose(f); return o;
}

static sqlite3_stmt *statement(const char *sql) {
    sqlite3_stmt *s = NULL;
    if (sqlite3_prepare_v2(music_db, sql, -1, &s, NULL) != SQLITE_OK) fail(sqlite3_errmsg(music_db));
    return s;
}
static void done(sqlite3_stmt *s) {
    if (sqlite3_step(s) != SQLITE_DONE) fail(sqlite3_errmsg(music_db)); sqlite3_finalize(s);
}
static cJSON *json_col(sqlite3_stmt *s, int n) {
    const char *t = (const char *)sqlite3_column_text(s, n);
    cJSON *j = t ? cJSON_Parse(t) : NULL; if (!j) fail("数据库 JSON 损坏"); return j;
}
static const char *str(cJSON *j, const char *key) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(j, key); return cJSON_IsString(v) ? v->valuestring : "";
}
static double number(cJSON *j, const char *key) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(j, key);
    return cJSON_IsNumber(v) ? v->valuedouble : cJSON_IsString(v) ? strtod(v->valuestring, NULL) : 0;
}
void open_catalog(void) {
    makedirs(music_home); char file[4200]; snprintf(file, sizeof(file), "%s/catalog.sqlite", music_home);
    if (sqlite3_open(file, &music_db) != SQLITE_OK) fail("无法打开数据库");
    sqlite3_busy_timeout(music_db, 60000);
    if (sqlite3_exec(music_db, "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;", NULL, NULL, NULL) != SQLITE_OK) fail(sqlite3_errmsg(music_db));
    sqlite3_stmt *q = NULL;
    if (sqlite3_prepare_v2(music_db, "PRAGMA user_version", -1, &q, NULL) != SQLITE_OK) fail(sqlite3_errmsg(music_db));
    if (sqlite3_step(q) != SQLITE_ROW) fail(sqlite3_errmsg(music_db));
    int version = sqlite3_column_int(q, 0); sqlite3_finalize(q);
    int count = (int)(sizeof(MIGRATIONS) / sizeof(MIGRATIONS[0]));
    if (version > count) fail("数据库版本高于工具；请升级工具");
    for (int i = version; i < count; i++)
        if (sqlite3_exec(music_db, MIGRATIONS[i], NULL, NULL, NULL) != SQLITE_OK) fail(sqlite3_errmsg(music_db));

}
void register_file(const char *path, char asset_id[65]) {
    char resolved[4096]; if (!realpath(path, resolved)) fail("源文件不存在");
    struct stat st; if (stat(resolved, &st) || !S_ISREG(st.st_mode)) fail("源不是普通文件");
    hash_file(resolved, asset_id);
    sqlite3_stmt *s = statement("SELECT 1 FROM assets WHERE id=?"); sqlite3_bind_text(s, 1, asset_id, -1, SQLITE_TRANSIENT);
    int exists = sqlite3_step(s) == SQLITE_ROW; sqlite3_finalize(s);
    if (!exists) {
        char *args[] = {"ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams", "-show_format", "-of", "json", resolved, NULL};
        char *raw = capture(args); cJSON *p = cJSON_Parse(raw); free(raw);
        if (!p) fail("ffprobe 返回无效 JSON");
        cJSON *stream = cJSON_GetArrayItem(cJSON_GetObjectItem(p, "streams"), 0);
        cJSON *format = cJSON_GetObjectItem(p, "format"); if (!stream) fail("文件不含音频流");
        cJSON *meta = cJSON_CreateObject();
        cJSON_AddStringToObject(meta, "sha256", asset_id); cJSON_AddStringToObject(meta, "codec", str(stream, "codec_name"));
        cJSON_AddStringToObject(meta, "format", str(format, "format_name"));
        const char *ext = strrchr(resolved, '.');
        cJSON_AddBoolToObject(meta, "extension_mismatch", ext && !strcasecmp(ext,".wav") && (strcmp(str(format,"format_name"),"wav") || !strcmp(str(stream,"codec_name"),"vorbis")));
        cJSON_AddNumberToObject(meta, "sample_rate", number(stream, "sample_rate"));
        cJSON_AddNumberToObject(meta, "channels", number(stream, "channels"));
        double duration = number(stream, "duration"); if (!duration) duration = number(format, "duration");
        cJSON_AddNumberToObject(meta, "duration_s", duration);
        cJSON_AddNumberToObject(meta, "container_start_s", number(stream, "start_time"));
        cJSON_AddItemToObject(meta, "embedded", embedded_tags(resolved));
        cJSON *bits=cJSON_GetObjectItem(stream,"bits_per_raw_sample");
        if(!bits) bits=cJSON_GetObjectItem(stream,"bits_per_sample");
        cJSON_AddItemToObject(meta,"bits_per_sample",bits ? cJSON_Duplicate(bits,1) : cJSON_CreateNull());
        char *text = cJSON_PrintUnformatted(meta);
        s = statement("INSERT OR IGNORE INTO assets VALUES(?,?)");
        sqlite3_bind_text(s, 1, asset_id, -1, SQLITE_TRANSIENT); sqlite3_bind_text(s, 2, text, -1, SQLITE_TRANSIENT); done(s);
        free(text); cJSON_Delete(meta); cJSON_Delete(p);
    }
    s = statement("INSERT INTO locations VALUES(?,?) ON CONFLICT(path) DO UPDATE SET asset_id=excluded.asset_id");
    sqlite3_bind_text(s, 1, resolved, -1, SQLITE_TRANSIENT); sqlite3_bind_text(s, 2, asset_id, -1, SQLITE_TRANSIENT); done(s);
}
void resolve_asset(const char *ref, char id[65]) {
    if (access(ref, R_OK) == 0) { register_file(ref, id); return; }
    size_t n = strlen(ref); if (!n || n > 64) fail("无效素材 ID");
    for (size_t i = 0; i < n; i++) if (!isxdigit((unsigned char)ref[i])) fail("无效素材 ID");
    char pattern[66]; snprintf(pattern, sizeof(pattern), "%s%%", ref);
    sqlite3_stmt *s = statement("SELECT id FROM assets WHERE id LIKE ? LIMIT 2");
    sqlite3_bind_text(s, 1, pattern, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(s) != SQLITE_ROW) fail("素材 ID 不存在");
    snprintf(id, 65, "%s", sqlite3_column_text(s, 0));
    if (sqlite3_step(s) == SQLITE_ROW) fail("素材 ID 前缀不唯一"); sqlite3_finalize(s);
}
cJSON *asset_json(const char *id) {
    cJSON *o = cJSON_CreateObject(), *paths = cJSON_CreateArray();
    cJSON_AddStringToObject(o, "id", id);
    sqlite3_stmt *s = statement("SELECT metadata FROM assets WHERE id=?"); sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(s) != SQLITE_ROW) fail("素材不存在");
    cJSON_AddItemToObject(o, "metadata", json_col(s, 0)); sqlite3_finalize(s);
    s = statement("SELECT path FROM locations WHERE asset_id=? ORDER BY path"); sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(s) == SQLITE_ROW) cJSON_AddItemToArray(paths, cJSON_CreateString((const char *)sqlite3_column_text(s, 0)));
    sqlite3_finalize(s); cJSON_AddItemToObject(o, "paths", paths);
    char audio[4300]; snprintf(audio, sizeof(audio), "%s/assets/%s/source.wav", music_home, id);
    cJSON_AddStringToObject(o, "audio", audio); return o;
}
cJSON *query_runs(const char *id) {
    cJSON *a = cJSON_CreateArray();
    sqlite3_stmt *s = statement("SELECT id,task,status,summary,created FROM runs WHERE asset_id=? ORDER BY created DESC");
    sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(s) == SQLITE_ROW) {
        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "id", (const char *)sqlite3_column_text(s, 0));
        cJSON_AddStringToObject(o, "asset_id", id);
        cJSON_AddStringToObject(o, "signature", (const char *)sqlite3_column_text(s, 0));
        cJSON_AddStringToObject(o, "task", (const char *)sqlite3_column_text(s, 1));
        cJSON_AddStringToObject(o, "status", (const char *)sqlite3_column_text(s, 2));
        cJSON_AddItemToObject(o, "summary", json_col(s, 3)); cJSON_AddNumberToObject(o, "created", sqlite3_column_double(s, 4));
        cJSON_AddItemToArray(a, o);
    }
    sqlite3_finalize(s); return a;
}
cJSON *query_summary(const char *id) {
    cJSON *o = cJSON_CreateObject(), *tasks = cJSON_CreateObject(), *runs = query_runs(id), *r;
    cJSON *asset = asset_json(id); cJSON_AddItemToObject(o, "asset", asset);
    cJSON_ArrayForEach(r, runs) {
        const char *name = str(r, "task"); if (cJSON_HasObjectItem(tasks, name)) continue;
        cJSON *v = cJSON_CreateObject(); cJSON_AddStringToObject(v, "status", str(r, "status"));
        cJSON_AddStringToObject(v, "run_id", str(r, "id"));
        cJSON *sum = cJSON_Duplicate(cJSON_GetObjectItem(r, "summary"), 1);
        cJSON_DeleteItemFromObject(sum, "provenance"); cJSON_DeleteItemFromObject(sum, "descriptor"); cJSON_DeleteItemFromObject(sum, "raw");
        const char *fields[] = {"beats", "downbeats", "times", "candidates", "repetitions", NULL};
        for (int i = 0; fields[i]; i++) {
            cJSON *a = cJSON_GetObjectItem(sum, fields[i]); int n = cJSON_GetArraySize(a);
            if (cJSON_IsArray(a) && n > 8) {
                char key[80]; snprintf(key, sizeof(key), "%s_count", fields[i]); cJSON_AddNumberToObject(sum, key, n);
                while (cJSON_GetArraySize(a) > 8) cJSON_DeleteItemFromArray(a, 8);
            }
        }
        cJSON_AddItemToObject(v, "summary", sum); cJSON_AddItemToObject(tasks, name, v);
    }
    cJSON_Delete(runs); cJSON_AddItemToObject(o, "tasks", tasks);
    cJSON *corrections = cJSON_CreateArray();
    sqlite3_stmt *s = statement("SELECT id,field,value,reason,created FROM corrections WHERE asset_id=? ORDER BY id");
    sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(s) == SQLITE_ROW) {
        cJSON *v = cJSON_CreateObject(); cJSON_AddNumberToObject(v, "id", sqlite3_column_int(s, 0));
        cJSON_AddStringToObject(v, "asset_id", id); cJSON_AddStringToObject(v, "field", (const char *)sqlite3_column_text(s, 1));
        cJSON_AddItemToObject(v, "value", json_col(s, 2)); cJSON_AddStringToObject(v, "reason", (const char *)sqlite3_column_text(s, 3));
        cJSON_AddNumberToObject(v, "created", sqlite3_column_double(s, 4)); cJSON_AddItemToArray(corrections, v);
    }
    sqlite3_finalize(s); cJSON_AddItemToObject(o, "corrections", corrections);
    const char *names[]={"envelope","timbre","onsets","pitch","rhythm","loop","notes","chords","structure","sounds","perception","stems","lyrics","relations",NULL};
    cJSON *missing=cJSON_AddArrayToObject(o,"missing_tasks");
    for(int i=0;names[i];i++) if(!cJSON_HasObjectItem(tasks,names[i])) cJSON_AddItemToArray(missing,cJSON_CreateString(names[i]));
    cJSON *ps=cJSON_GetObjectItem(cJSON_GetObjectItem(tasks,"pitch"),"summary");
    cJSON *root=cJSON_GetObjectItem(cJSON_GetObjectItem(ps,"root"),"midi");
    cJSON *metadata=cJSON_GetObjectItem(asset,"metadata");
    cJSON *embedded=cJSON_GetObjectItem(cJSON_GetObjectItem(metadata,"embedded"),"root_midi");
    if(cJSON_IsNumber(root)&&cJSON_IsNumber(embedded)&&fabs(root->valuedouble-embedded->valuedouble)>.6) {
        cJSON *conflicts=cJSON_AddArrayToObject(o,"conflicts"), *v=cJSON_CreateObject();
        cJSON_AddStringToObject(v,"field","root_midi"); cJSON_AddNumberToObject(v,"embedded",embedded->valuedouble);
        cJSON_AddNumberToObject(v,"estimated",root->valuedouble); cJSON_AddStringToObject(v,"resolution","needs_verification");
        cJSON_AddItemToArray(conflicts,v);
    }
    cJSON_AddBoolToObject(o, "auditory_review", 0); cJSON_AddBoolToObject(o, "probabilities_calibrated", 0);
    return o;
}
cJSON *query_events(const char *id, double start, double end, const char *part, const char *type, int limit, int offset, const char *task) {
    const char *sql = "WITH latest AS (SELECT id,status,task,row_number() OVER(PARTITION BY task ORDER BY created DESC) AS n FROM runs WHERE asset_id=?) "
      "SELECT payload FROM events WHERE run_id IN (SELECT id FROM latest WHERE n=1 AND status='complete') "
      "AND t1>=? AND t0<? AND (? IS NULL OR part=?) AND (? IS NULL OR type=?) AND (?10 IS NULL OR run_id IN (SELECT id FROM latest WHERE task=?10)) "
      "AND (?10 IS NOT NULL OR type!='note' OR run_id NOT IN (SELECT id FROM latest WHERE task='pitch') OR NOT EXISTS "
      "(SELECT 1 FROM events n WHERE n.asset_id=events.asset_id AND n.part=events.part AND n.type='note' AND n.run_id IN "
      "(SELECT id FROM latest WHERE task='notes' AND n=1 AND status='complete'))) ORDER BY t0,id LIMIT ?8 OFFSET ?9";
    sqlite3_stmt *s = statement(sql); sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT);
    sqlite3_bind_double(s, 2, start); sqlite3_bind_double(s, 3, end);
    for (int i = 4; i <= 5; i++) if (part) sqlite3_bind_text(s, i, part, -1, SQLITE_TRANSIENT); else sqlite3_bind_null(s, i);
    for (int i = 6; i <= 7; i++) if (type) sqlite3_bind_text(s, i, type, -1, SQLITE_TRANSIENT); else sqlite3_bind_null(s, i);
    if (task) sqlite3_bind_text(s, 10, task, -1, SQLITE_TRANSIENT); else sqlite3_bind_null(s, 10);
    sqlite3_bind_int(s, 8, limit + 1); sqlite3_bind_int(s, 9, offset);
    cJSON *events = cJSON_CreateArray(), *o = cJSON_CreateObject();
    while (sqlite3_step(s) == SQLITE_ROW) cJSON_AddItemToArray(events, json_col(s, 0)); sqlite3_finalize(s);
    if (cJSON_GetArraySize(events) > limit) { cJSON_DeleteItemFromArray(events, limit); cJSON_AddNumberToObject(o, "next_offset", offset + limit); }
    else cJSON_AddNullToObject(o, "next_offset");
    cJSON_AddItemToObject(o, "events", events); return o;
}
cJSON *query_evidence(const char *id, const char *event) {
    if (!event) fail("evidence 需要 --id");
    sqlite3_stmt *s = statement("SELECT payload FROM events WHERE asset_id=? AND id=?");
    sqlite3_bind_text(s, 1, id, -1, SQLITE_TRANSIENT); sqlite3_bind_text(s, 2, event, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(s) != SQLITE_ROW) fail("该事件不存在或不属于指定素材");
    cJSON *o = json_col(s, 0); sqlite3_finalize(s); return o;
}
cJSON *search_paths(const char *text, int limit) {
    sqlite3_stmt *s = statement("SELECT asset_id,json_group_array(path) FROM locations WHERE instr(lower(path),lower(?))>0 GROUP BY asset_id ORDER BY min(path) LIMIT ?");
    sqlite3_bind_text(s, 1, text, -1, SQLITE_TRANSIENT); sqlite3_bind_int(s, 2, limit);
    cJSON *o = cJSON_CreateObject(), *a = cJSON_CreateArray();
    while (sqlite3_step(s) == SQLITE_ROW) {
        cJSON *r = cJSON_CreateObject(); cJSON_AddStringToObject(r, "asset_id", (const char *)sqlite3_column_text(s, 0));
        cJSON_AddItemToObject(r, "paths", json_col(s, 1)); cJSON_AddItemToArray(a, r);
    }
    sqlite3_finalize(s); cJSON_AddStringToObject(o, "mode", "filename_substring_not_semantic"); cJSON_AddItemToObject(o, "matches", a); return o;
}
static int scan_count, scan_limit;
static size_t scan_root_length;
static int visit(const char *path, const struct stat *st, int flag, struct FTW *f) {
    (void)st;
    if (flag != FTW_F || path[f->base] == '.' || strstr(path+scan_root_length,"/.")) return 0;
    const char *ext = strrchr(path, '.'); if (!ext) return 0;
    const char *allowed[] = {".wav", ".flac", ".mp3", ".aif", ".aiff", ".m4a", ".ogg", ".opus", ".caf", ".mp4", NULL};
    int yes = 0; for (int i = 0; allowed[i]; i++) if (!strcasecmp(ext, allowed[i])) yes = 1;
    if (!yes) return 0;
    if (scan_limit >= 0 && scan_count >= scan_limit) return 1;
    char id[65]; register_file(path, id); scan_count++;
    sqlite3_stmt *s=statement("INSERT OR IGNORE INTO scanned VALUES(?)");
    sqlite3_bind_text(s,1,id,-1,SQLITE_TRANSIENT); done(s);
    if (!(scan_count % 100)) fprintf(stderr, "已索引 %d 个音频\n", scan_count);
    return 0;
}
cJSON *index_directory(const char *root, int limit) {
    char real[4096]; if (!realpath(root, real)) fail("采样目录不存在");
    struct stat st; if (stat(real, &st) || !S_ISDIR(st.st_mode)) fail("输入不是目录");
    size_t n = strlen(real);
    if (!strncmp(real, music_home, n) && (music_home[n] == '/' || music_home[n] == 0)) fail("缓存目录必须在采样库外");
    scan_count = 0; scan_limit = limit; scan_root_length=n;
    if(sqlite3_exec(music_db,"CREATE TEMP TABLE scanned(id TEXT PRIMARY KEY)",NULL,NULL,NULL)!=SQLITE_OK) fail(sqlite3_errmsg(music_db));
    if (nftw(real, visit, 16, FTW_PHYS) < 0) fail("遍历采样目录失败");
    cJSON *o = cJSON_CreateObject(); cJSON_AddStringToObject(o, "root", real); cJSON_AddNumberToObject(o, "files", scan_count);
    sqlite3_stmt *s=statement("SELECT count(*) FROM scanned");
    if(sqlite3_step(s)!=SQLITE_ROW) fail(sqlite3_errmsg(music_db));
    int unique=sqlite3_column_int(s,0); sqlite3_finalize(s);
    cJSON_AddNumberToObject(o,"unique_audio_assets",unique); cJSON_AddNumberToObject(o,"duplicate_paths",scan_count-unique);
    cJSON_AddArrayToObject(o,"failures");
    cJSON_AddStringToObject(o, "engine", "c17"); return o;
}
