/* YIN difference function + cumulative mean normalization, with explicit voicing score.
   Own implementation of the algorithm, not copied from a model/library. */
#include <math.h>
#include <stddef.h>
#include <stdlib.h>

int music_pitch_frame(const float *x, size_t length, double sr, double low_hz, double high_hz,
                      double *hz, double *aperiodicity) {
    if (!x || !hz || !aperiodicity || !isfinite(sr) || !isfinite(low_hz) || !isfinite(high_hz) ||
        sr <= 0 || low_hz < sr / 16384 || high_hz <= low_hz || high_hz >= sr / 2) return -1;
    size_t maxlag = (size_t)(sr / low_hz), minlag = (size_t)(sr / high_hz);
    if (minlag < 2) minlag = 2;
    if (maxlag > 16384 || length <= maxlag + 32 || maxlag <= minlag) return -1;
    size_t window = length - maxlag;
    double *d = calloc(maxlag + 1, sizeof(double)); if (!d) return -1;
    double total = 0;
    for (size_t lag = 1; lag <= maxlag; lag++) {
        double difference = 0;
        for (size_t i = 0; i < window; i++) {
            double delta = (double)x[i] - x[i + lag]; difference += delta * delta;
        }
        total += difference;
        d[lag] = total > 1e-20 ? difference * lag / total : 1;
    }
    size_t best = minlag; int threshold = 0;
    for (size_t i = minlag; i < maxlag; i++) {
        if (d[i] < d[best]) best = i;
        if (d[i] < .1) { best = i; threshold = 1; break; }
    }
    if (threshold) while (best < maxlag && d[best + 1] < d[best]) best++;
    double lag = (double)best;
    if (best > minlag && best < maxlag) {
        double denom = d[best - 1] - 2 * d[best] + d[best + 1];
        if (fabs(denom) > 1e-12) lag += fmax(-.5, fmin(.5, .5 * (d[best - 1] - d[best + 1]) / denom));
    }
    *hz = sr / lag; *aperiodicity = d[best]; free(d); return 0;
}
