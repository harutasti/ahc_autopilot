#include <chrono>
#include <cstdint>
#include <iostream>
using namespace std;

struct Timer {
    chrono::steady_clock::time_point start;
    double limit_sec;

    explicit Timer(double limit_sec_) : start(chrono::steady_clock::now()), limit_sec(limit_sec_) {}

    double elapsed() const {
        auto now = chrono::steady_clock::now();
        chrono::duration<double> diff = now - start;
        return diff.count();
    }

    bool expired() const { return elapsed() >= limit_sec; }
};

struct XorShift64 {
    uint64_t x;

    explicit XorShift64(uint64_t seed = 88172645463325252ull) : x(seed) {}

    uint64_t next_u64() {
        x ^= x << 7;
        x ^= x >> 9;
        return x;
    }

    int next_int(int low, int high) {
        return low + static_cast<int>(next_u64() % static_cast<uint64_t>(high - low + 1));
    }

    double next_double() {
        return (next_u64() >> 11) * (1.0 / 9007199254740992.0);
    }
};

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    Timer timer(1.95);
    XorShift64 rng(123456789);

    long long target = 0;
    if (!(cin >> target)) {
        return 0;
    }

    // Dummy baseline: reproduce the input target exactly.
    // Replace this with the contest-specific solver.
    cout << target << '\n';

    (void)timer;
    (void)rng;
    return 0;
}
