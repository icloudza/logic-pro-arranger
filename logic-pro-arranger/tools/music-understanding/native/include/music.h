#ifndef MUSIC_H
#define MUSIC_H
#include <sqlite3.h>
#include "cJSON.h"

extern sqlite3 *music_db;
extern char music_home[4096];
void fail(const char *message);
void emit(cJSON *value);
void makedirs(const char *path);
void normalize_path(const char *path, char out[4096]);
char *capture(char *const argv[]);
void hash_file(const char *path, char hex[65]);
void hash_bytes(const void *data, size_t n, char hex[65]);
void open_catalog(void);
void register_file(const char *path, char asset_id[65]);
void resolve_asset(const char *ref, char id[65]);
cJSON *asset_json(const char *id);
cJSON *query_summary(const char *id);
cJSON *query_events(const char *id, double start, double end, const char *part, const char *type, int limit, int offset, const char *task);
cJSON *query_evidence(const char *id, const char *event);
cJSON *query_runs(const char *id);
cJSON *search_paths(const char *text, int limit);
cJSON *index_directory(const char *root, int limit);
cJSON *dsp_file(const char *path, double fmin, double fmax, int pitch);
const char *option(int argc, char **argv, const char *key, const char *fallback);
#endif
