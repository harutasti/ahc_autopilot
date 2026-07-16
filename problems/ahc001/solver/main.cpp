#include <iostream>
#include <vector>
using namespace std;

// AHC001 baseline: give every requested point a 1x1 rectangle. Points are
// unique, so these rectangles have positive area and never overlap -- a valid
// (low-scoring) starting solver, kept separate from other problems' sources.
int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    int n;
    if (!(cin >> n)) {
        return 0;
    }
    for (int i = 0; i < n; i++) {
        int x, y, r;
        cin >> x >> y >> r;
        cout << x << ' ' << y << ' ' << x + 1 << ' ' << y + 1 << '\n';
    }
    return 0;
}
