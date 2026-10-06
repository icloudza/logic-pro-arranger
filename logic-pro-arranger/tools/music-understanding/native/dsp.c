#include "music.h"
#define DR_WAV_IMPLEMENTATION
#include "dr_wav.h"
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int music_pitch_frame(const float *, size_t, double, double, double, double *, double *);
static int compare_double(const void *a, const void *b) { double x=*(const double *)a,y=*(const double *)b; return (x>y)-(x<y); }

cJSON *dsp_file(const char *path, double low, double high, int pitch) {
    drwav wav;
    if (!drwav_init_file(&wav, path, NULL)) fail("C DSP 需要标准 WAV；请先用 analyze 统一解码");
    unsigned sr = wav.sampleRate, channels = wav.channels;
    drwav_uint64 count = wav.totalPCMFrameCount;
    if (!count || channels > 64 || count > SIZE_MAX / channels / sizeof(float) || count * channels > 500000000) {
        drwav_uninit(&wav); fail("WAV 为空或超过单次 DSP 内存限制");
    }
    float *raw = malloc((size_t)count * channels * sizeof(float));
    float *y = malloc((size_t)count * sizeof(float));
    double *energy = calloc(channels, sizeof(double));
    if (!raw || !y || !energy) fail("内存不足");
    if (drwav_read_pcm_frames_f32(&wav, count, raw) != count) fail("WAV 数据不完整");
    drwav_uninit(&wav); double mixenergy = 0;
    for (size_t i = 0; i < count; i++) {
        double mix = 0;
        for (unsigned c = 0; c < channels; c++) {
            double v = raw[i * channels + c]; if (!isfinite(v)) fail("音频包含非有限值");
            energy[c] += v * v; mix += v;
        }
        y[i] = (float)(mix / channels); mixenergy += (double)y[i] * y[i];
    }
    unsigned best = 0; for (unsigned c = 1; c < channels; c++) if (energy[c] > energy[best]) best = c;
    int cancellation = mixenergy < energy[best] * .05;
    if (cancellation) for (size_t i = 0; i < count; i++) y[i] = raw[i * channels + best];
    free(energy);
    double peak = 0, sum = 0; size_t peak_index = 0, clipped = 0;
    for (size_t i = 0; i < count * channels; i++) {
        double v = raw[i]; sum += v * v;
        if (fabs(v) > peak) { peak = fabs(v); peak_index = i / channels; }
        if (fabs(v) >= 1) clipped++;
    }
    free(raw);
    cJSON *o = cJSON_CreateObject(); cJSON_AddStringToObject(o, "engine", "c17");
    cJSON_AddNumberToObject(o, "sample_rate", sr); cJSON_AddNumberToObject(o, "channels", channels);
    cJSON_AddNumberToObject(o, "duration_s", (double)count / sr);
    cJSON_AddNumberToObject(o, "peak_dbfs", 20 * log10(fmax(peak, 1e-12)));
    cJSON_AddNumberToObject(o, "rms_dbfs", 20 * log10(fmax(sqrt(sum / (count * channels)), 1e-12)));
    cJSON_AddNumberToObject(o, "peak_at_s", (double)peak_index / sr);
    cJSON_AddNumberToObject(o, "clipping_sample_fraction", (double)clipped / (count * channels));
    cJSON_AddStringToObject(o, "measurement_domain", "original_decoded_samples_all_channels");
    cJSON_AddBoolToObject(o, "silence", peak < 1e-5);
    cJSON_AddStringToObject(o, "mono_policy", cancellation ? "highest_energy_channel_due_to_cancellation" : "channel_mean");
    if (pitch) {
        if (!(low >= sr / 16384. && high > low && high < sr / 2)) fail("音高范围无效");
        size_t hop = sr / 100, window = sr / 20 + (size_t)(sr / low);
        if (!hop) hop = 1;
        size_t maxframes = count / hop + 1;
        double *midis = malloc(maxframes * sizeof(double)); size_t voiced = 0, frames = 0;
        if (!midis) fail("内存不足");
        cJSON *contour = cJSON_CreateArray();
        for (size_t at = 0; at + window <= count; at += hop) {
            double rms = 0; for (size_t k = 0; k < window; k++) rms += (double)y[at+k] * y[at+k];
            rms = sqrt(rms / window); double hz = 0, ap = 1;
            if (rms > fmax(sqrt(sum / (count * channels)) * .05, 1e-5)) music_pitch_frame(y + at, window, sr, low, high, &hz, &ap);
            double t = ((double)at + (window - sr / low) / 2) / sr;
            int valid = hz > 0 && ap < .15;
            if (valid && t > .1) midis[voiced++] = 69 + 12 * log2(hz / 440);
            double v[] = {t, hz, ap, valid}; cJSON_AddItemToArray(contour, cJSON_CreateDoubleArray(v, 4)); frames++;
        }
        cJSON_AddItemToObject(o, "contour", contour);
        cJSON_AddNumberToObject(o, "frame_window_s", (double)window / sr);
        cJSON_AddNumberToObject(o, "periodic_coverage", frames ? (double)voiced / frames : 0);
        cJSON_AddNullToObject(o, "probability");
        qsort(midis, voiced, sizeof(double), compare_double);
        if (voiced >= 12 && midis[(voiced-1)*9/10] - midis[(voiced-1)/10] < .65) {
            cJSON *root = cJSON_CreateObject();
            double m = midis[voiced/2]; cJSON_AddNumberToObject(root, "midi", m);
            cJSON_AddNumberToObject(root, "nearest_midi", round(m));
            cJSON_AddStringToObject(root, "status", "candidate_not_auditory_verified"); cJSON_AddItemToObject(o, "root", root);
        } else cJSON_AddNullToObject(o, "root");
        free(midis);
    }
    free(y); return o;
}
