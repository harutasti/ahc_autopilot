#include <bits/stdc++.h>
using namespace std;
using namespace std::chrono;

// AHC001: simulated annealing over per-company rectangle boundaries.
//
// State: for each company i, an axis-aligned rectangle [ra[i],rc[i]) x
// [rb[i],rd[i]) that must contain (X[i],Y[i]) as its "requested point" cell.
// Rectangles must stay pairwise non-overlapping at all times.
//
// Move set: pick one company and one of its four edges, then resample that
// edge's coordinate uniformly within the widest range that (a) still keeps
// the requested point inside and (b) does not collide with any other
// rectangle's *current* position. Because every other rectangle's bounds are
// left untouched by such a move, its area/satisfaction is provably
// unaffected -- only company i's score can change. That lets scoring be O(1)
// incremental (delta = new_p - old_p folded into a running sum) instead of
// an O(n) full rescore, and rejection is an O(1) rollback of the single
// coordinate that was tentatively written. The only O(n) part of a move is
// the collision scan needed to bound the edge's feasible range -- that is
// unavoidable geometry, not rescoring.

static uint64_t rng_state;
static inline uint64_t xorshift64() {
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 7;
    rng_state ^= rng_state << 17;
    return rng_state;
}
static inline int randint(int lo, int hi) { // inclusive, lo<=hi
    if (lo >= hi) return lo;
    return lo + (int)(xorshift64() % (uint64_t)(hi - lo + 1));
}
static inline double randdouble01() {
    return (double)(xorshift64() >> 11) * (1.0 / 9007199254740992.0);
}

int n;
vector<int> X, Y, R;
vector<int> ra, rb, rc, rd; // rectangle i occupies [ra,rc) x [rb,rd)
vector<double> P;
double sumP;

static inline double satisfaction(long long r, long long s) {
    double ratio = (double)min(r, s) / (double)max(r, s);
    double t = 1.0 - ratio;
    return 1.0 - t * t;
}

static inline long long areaOf(int i) {
    return (long long)(rc[i] - ra[i]) * (long long)(rd[i] - rb[i]);
}

// Feasible [lo, hi] range for edge e of rect i, holding all other rects and
// i's other three edges fixed. e: 0=left(a) 1=right(c) 2=bottom(b) 3=top(d).
static inline pair<int, int> edgeRange(int i, int e) {
    int lo, hi;
    if (e == 0) {
        hi = X[i];
        lo = 0;
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            if (rb[j] < rd[i] && rd[j] > rb[i]) { // y-ranges overlap
                if (rc[j] <= ra[i] && rc[j] > lo) lo = rc[j];
            }
        }
    } else if (e == 1) {
        lo = X[i] + 1;
        hi = 10000;
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            if (rb[j] < rd[i] && rd[j] > rb[i]) {
                if (ra[j] >= rc[i] && ra[j] < hi) hi = ra[j];
            }
        }
    } else if (e == 2) {
        hi = Y[i];
        lo = 0;
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            if (ra[j] < rc[i] && rc[j] > ra[i]) { // x-ranges overlap
                if (rd[j] <= rb[i] && rd[j] > lo) lo = rd[j];
            }
        }
    } else {
        lo = Y[i] + 1;
        hi = 10000;
        for (int j = 0; j < n; j++) {
            if (j == i) continue;
            if (ra[j] < rc[i] && rc[j] > ra[i]) {
                if (rb[j] >= rd[i] && rb[j] < hi) hi = rb[j];
            }
        }
    }
    return {lo, hi};
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    auto t_start = steady_clock::now();
    const double TIME_LIMIT = 4.5; // seconds; contest limit is 5s, leave safety margin

    if (!(cin >> n)) return 0;
    X.resize(n);
    Y.resize(n);
    R.resize(n);
    ra.resize(n);
    rb.resize(n);
    rc.resize(n);
    rd.resize(n);
    P.resize(n);
    for (int i = 0; i < n; i++) {
        cin >> X[i] >> Y[i] >> R[i];
        ra[i] = X[i];
        rb[i] = Y[i];
        rc[i] = X[i] + 1;
        rd[i] = Y[i] + 1;
    }

    sumP = 0.0;
    for (int i = 0; i < n; i++) {
        P[i] = satisfaction(R[i], areaOf(i));
        sumP += P[i];
    }

    rng_state = 88172645463325252ULL; // fixed seed: deterministic move sequence

    vector<int> best_a = ra, best_b = rb, best_c = rc, best_d = rd;
    double bestSum = sumP;

    const double T0 = 0.3, T1 = 1e-4;
    long long iter = 0;
    double elapsed = 0.0;
    while (true) {
        if ((iter & 0x3F) == 0) {
            elapsed = duration<double>(steady_clock::now() - t_start).count();
            if (elapsed > TIME_LIMIT) break;
        }
        iter++;

        int i = randint(0, n - 1);
        int e = randint(0, 3);
        auto [lo, hi] = edgeRange(i, e);
        if (lo >= hi) continue;
        int newVal = randint(lo, hi);

        int *coord = (e == 0) ? &ra[i] : (e == 1) ? &rc[i] : (e == 2) ? &rb[i] : &rd[i];
        int oldVal = *coord;
        if (newVal == oldVal) continue;

        double oldP = P[i];
        *coord = newVal;
        double newP = satisfaction(R[i], areaOf(i));
        double delta = newP - oldP;

        double frac = min(1.0, elapsed / TIME_LIMIT);
        double T = T0 * pow(T1 / T0, frac);
        bool accept = (delta >= 0.0) || (randdouble01() < exp(delta / T));

        if (accept) {
            P[i] = newP;
            sumP += delta;
            if (sumP > bestSum) {
                bestSum = sumP;
                best_a = ra;
                best_b = rb;
                best_c = rc;
                best_d = rd;
            }
        } else {
            *coord = oldVal; // O(1) rollback; P[i]/sumP were never touched
        }
    }

    for (int i = 0; i < n; i++) {
        cout << best_a[i] << ' ' << best_b[i] << ' ' << best_c[i] << ' ' << best_d[i] << '\n';
    }
    return 0;
}
