#include <chrono>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <numeric>
#include <queue>
#include <sstream>
#include <string>
#include <vector>
using namespace std;

// Tunable constants: overridable via AHC_PARAM_* environment variables (the
// `ahc tune` contract). Defaults are the shipped values, so behavior without
// the variables is unchanged; hardcode winners before submitting to AtCoder.
static double param_double(const char *name, double fallback) {
    const char *env = getenv(name);
    return env ? atof(env) : fallback;
}

static int param_int(const char *name, int fallback) {
    const char *env = getenv(name);
    return env ? atoi(env) : fallback;
}

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

struct AHC067Solver {
    static constexpr int MAX_N = 20;
    static constexpr int MAX_K = 10;
    static constexpr int MAX_STATES = MAX_N * MAX_N * (1 << MAX_K);
    static constexpr int INF = 1 << 28;

    struct Edge {
        int u;
        int v;
        int d;
        int i;
        int j;
    };

    struct Adj {
        int to;
        int edge;
    };

    struct Gate {
        vector<int> edges;
        int sw;
    };

    struct Candidate {
        int priority;
        vector<int> edges;
        int sw;
    };

    // General door/switch plan: explicit (edge, door-type) and (cell,
    // switch-type) pairs. Unlike Gate (which always uses door type
    // 2*idx+1 for the gate at position idx), this can express a single
    // switch controlling both door parities (2k and 2k+1) across
    // different edges, which the binary-counter construction needs.
    struct Plan {
        vector<pair<int, int>> door_edge_type;
        vector<pair<int, int>> switch_cell_type;
    };

    // A dead-end pocket reached from the hub via a serial sequence of
    // levels, ordered hub-to-deep. Each level is a SET of edges gated by
    // the same door type once the pocket is consumed for a ring at least
    // that deep. A natural pocket (see find_pockets) has exactly one edge
    // per level -- a genuine graph bridge, verified internally cycle-free
    // when require_tree is set. A manufactured pocket (see
    // find_cut_pockets) can have several edges per level: doors of the
    // same type share open/close state, so sealing every entrance edge of
    // a multi-entrance region with that type collapses the whole region
    // into a single controllable gate, even though no single edge among
    // them is a graph bridge on its own.
    struct Pocket {
        vector<vector<int>> level_edges;
        int switch_cell;
    };

    int n;
    int m;
    int k;
    vector<string> grid;
    vector<pair<int, int>> cells;
    int cell_id[MAX_N][MAX_N];
    vector<Edge> edges;
    vector<vector<Adj>> graph;
    int start_id;
    int goal_id;
    vector<int> seen_stamp;
    vector<int> dist_state;
    int stamp = 1;
    XorShift64 rng;

    AHC067Solver(int n_, int m_, int k_, vector<string> grid_)
        : n(n_), m(m_), k(k_), grid(std::move(grid_)), rng(987654321ull) {
        for (int i = 0; i < MAX_N; i++) {
            for (int j = 0; j < MAX_N; j++) {
                cell_id[i][j] = -1;
            }
        }
        build_graph();
        seen_stamp.assign(MAX_STATES, 0);
        dist_state.assign(MAX_STATES, 0);
    }

    void build_graph() {
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                if (grid[i][j] == '.') {
                    cell_id[i][j] = static_cast<int>(cells.size());
                    cells.push_back({i, j});
                }
            }
        }
        graph.assign(cells.size(), {});
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                int u = cell_id[i][j];
                if (u < 0) continue;
                if (i + 1 < n && cell_id[i + 1][j] >= 0) {
                    add_edge(u, cell_id[i + 1][j], 0, i, j);
                }
                if (j + 1 < n && cell_id[i][j + 1] >= 0) {
                    add_edge(u, cell_id[i][j + 1], 1, i, j);
                }
            }
        }
        start_id = cell_id[0][0];
        goal_id = cell_id[n - 1][n - 1];
    }

    void add_edge(int u, int v, int d, int i, int j) {
        int idx = static_cast<int>(edges.size());
        edges.push_back({u, v, d, i, j});
        graph[u].push_back({v, idx});
        graph[v].push_back({u, idx});
    }

    vector<int> bfs_cell(int src, int banned_edge = -1) const {
        vector<int> dist(cells.size(), -1);
        queue<int> q;
        dist[src] = 0;
        q.push(src);
        while (!q.empty()) {
            int u = q.front();
            q.pop();
            for (const Adj &a : graph[u]) {
                if (a.edge == banned_edge || dist[a.to] >= 0) continue;
                dist[a.to] = dist[u] + 1;
                q.push(a.to);
            }
        }
        return dist;
    }

    void bridge_dfs(int u, int parent_edge, vector<int> &tin, vector<int> &low, int &timer,
                    vector<int> &bridges) const {
        tin[u] = low[u] = timer++;
        for (const Adj &a : graph[u]) {
            if (a.edge == parent_edge) continue;
            if (tin[a.to] >= 0) {
                low[u] = min(low[u], tin[a.to]);
            } else {
                bridge_dfs(a.to, a.edge, tin, low, timer, bridges);
                low[u] = min(low[u], low[a.to]);
                if (low[a.to] > tin[u]) {
                    bridges.push_back(a.edge);
                }
            }
        }
    }

    vector<int> start_side_without_edge(int banned_edge) const {
        vector<int> side(cells.size(), 0);
        queue<int> q;
        side[start_id] = 1;
        q.push(start_id);
        while (!q.empty()) {
            int u = q.front();
            q.pop();
            for (const Adj &a : graph[u]) {
                if (a.edge == banned_edge || side[a.to]) continue;
                side[a.to] = 1;
                q.push(a.to);
            }
        }
        return side;
    }

    static void sort_and_trim_candidates(vector<Candidate> &candidates, int max_candidates) {
        sort(candidates.begin(), candidates.end(), [](const Candidate &a, const Candidate &b) {
            if (a.priority != b.priority) return a.priority > b.priority;
            if (a.edges.size() != b.edges.size()) return a.edges.size() < b.edges.size();
            if (a.edges != b.edges) return a.edges < b.edges;
            return a.sw < b.sw;
        });
        if (static_cast<int>(candidates.size()) > max_candidates) {
            candidates.resize(max_candidates);
        }
    }

    vector<Candidate> make_bridge_candidates(int top_switches_per_bridge, int max_candidates) const {
        vector<int> tin(cells.size(), -1), low(cells.size(), 0), bridges;
        int dfs_timer = 0;
        bridge_dfs(start_id, -1, tin, low, dfs_timer, bridges);

        const vector<int> dist_start = bfs_cell(start_id);
        vector<Candidate> candidates;
        for (int edge_idx : bridges) {
            vector<int> side = start_side_without_edge(edge_idx);
            if (side[goal_id]) continue;

            int a = edges[edge_idx].u;
            int b = edges[edge_idx].v;
            if (!side[a]) swap(a, b);
            vector<int> dist_anchor = bfs_cell(a, edge_idx);

            vector<pair<int, int>> ranked;
            ranked.reserve(cells.size());
            for (int s = 0; s < static_cast<int>(cells.size()); s++) {
                if (!side[s] || dist_start[s] < 0 || dist_anchor[s] < 0) continue;
                int detour = dist_start[s] + dist_anchor[s] + 1;
                ranked.push_back({detour, s});
            }
            sort(ranked.rbegin(), ranked.rend());
            for (int idx = 0; idx < top_switches_per_bridge && idx < static_cast<int>(ranked.size()); idx++) {
                candidates.push_back({ranked[idx].first, vector<int>{edge_idx}, ranked[idx].second});
            }
        }
        sort_and_trim_candidates(candidates, max_candidates);
        return candidates;
    }

    vector<Candidate> make_layer_cut_candidates(int top_switches_per_layer, int max_candidates) const {
        const vector<int> dist_start = bfs_cell(start_id);
        if (dist_start[goal_id] <= 1) return {};

        vector<Candidate> candidates;
        const int max_edges_per_cut = min(m, 30);
        for (int layer = 1; layer < dist_start[goal_id]; layer++) {
            vector<int> cut_edges;
            cut_edges.reserve(max_edges_per_cut + 1);
            for (int edge_idx = 0; edge_idx < static_cast<int>(edges.size()); edge_idx++) {
                int u = edges[edge_idx].u;
                int v = edges[edge_idx].v;
                bool us = dist_start[u] >= 0 && dist_start[u] <= layer;
                bool vs = dist_start[v] >= 0 && dist_start[v] <= layer;
                if (us == vs) continue;
                cut_edges.push_back(edge_idx);
                if (static_cast<int>(cut_edges.size()) > max_edges_per_cut) break;
            }
            if (static_cast<int>(cut_edges.size()) < 2 ||
                static_cast<int>(cut_edges.size()) > max_edges_per_cut) {
                continue;
            }

            vector<int> dist_frontier(cells.size(), -1);
            queue<int> q;
            for (int edge_idx : cut_edges) {
                int u = edges[edge_idx].u;
                int v = edges[edge_idx].v;
                int inside = (dist_start[u] <= layer ? u : v);
                if (dist_frontier[inside] >= 0) continue;
                dist_frontier[inside] = 0;
                q.push(inside);
            }
            while (!q.empty()) {
                int u = q.front();
                q.pop();
                for (const Adj &a : graph[u]) {
                    if (dist_start[a.to] < 0 || dist_start[a.to] > layer || dist_frontier[a.to] >= 0) continue;
                    dist_frontier[a.to] = dist_frontier[u] + 1;
                    q.push(a.to);
                }
            }

            vector<pair<int, int>> ranked;
            ranked.reserve(cells.size());
            for (int s = 0; s < static_cast<int>(cells.size()); s++) {
                if (dist_start[s] < 0 || dist_start[s] > layer || dist_frontier[s] < 0) continue;
                int route_cost = dist_start[s] + dist_frontier[s];
                int priority = route_cost * 100 + min(99, dist_frontier[s]) +
                               static_cast<int>(cut_edges.size());
                ranked.push_back({priority, s});
            }
            sort(ranked.rbegin(), ranked.rend());
            sort(cut_edges.begin(), cut_edges.end());
            for (int idx = 0; idx < top_switches_per_layer && idx < static_cast<int>(ranked.size()); idx++) {
                candidates.push_back({ranked[idx].first, cut_edges, ranked[idx].second});
            }
        }
        sort_and_trim_candidates(candidates, max_candidates);
        return candidates;
    }

    int evaluate(const vector<Gate> &solution) {
        vector<int> door_type(edges.size(), -1);
        vector<int> switch_type(cells.size(), -1);
        for (int idx = 0; idx < static_cast<int>(solution.size()); idx++) {
            for (int edge_idx : solution[idx].edges) {
                door_type[edge_idx] = 2 * idx + 1;
            }
            switch_type[solution[idx].sw] = idx;
        }
        return evaluate_arrays(door_type, switch_type);
    }

    // Exact BFS over (position, switch-parity-mask) states, taking explicit
    // per-edge door types and per-cell switch types directly. This is the
    // general form evaluate(vector<Gate>) builds its arrays for; it also
    // backs plans that need both door parities (2k open-by-default and
    // 2k+1 closed-by-default) on the same switch k, which the Gate/idx
    // convention (door type always 2*idx+1) cannot express.
    int evaluate_arrays(const vector<int> &door_type, const vector<int> &switch_type) {
        if (++stamp == INF) {
            fill(seen_stamp.begin(), seen_stamp.end(), 0);
            stamp = 1;
        }

        vector<int> q;
        q.reserve(cells.size() * (1 << k));
        int start_state = start_id;
        seen_stamp[start_state] = stamp;
        dist_state[start_state] = 0;
        q.push_back(start_state);

        for (size_t head = 0; head < q.size(); head++) {
            int state = q[head];
            int mask = state / static_cast<int>(cells.size());
            int u = state - mask * static_cast<int>(cells.size());
            int cur_dist = dist_state[state];
            if (u == goal_id) {
                return cur_dist;
            }

            int sw = switch_type[u];
            if (sw >= 0) {
                int next_state = u + static_cast<int>(cells.size()) * (mask ^ (1 << sw));
                if (seen_stamp[next_state] != stamp) {
                    seen_stamp[next_state] = stamp;
                    dist_state[next_state] = cur_dist + 1;
                    q.push_back(next_state);
                }
            }

            for (const Adj &a : graph[u]) {
                int g = door_type[a.edge];
                if (g >= 0) {
                    int bit = (mask >> (g / 2)) & 1;
                    if (bit != (g & 1)) continue;
                }
                int next_state = a.to + static_cast<int>(cells.size()) * mask;
                if (seen_stamp[next_state] != stamp) {
                    seen_stamp[next_state] = stamp;
                    dist_state[next_state] = cur_dist + 1;
                    q.push_back(next_state);
                }
            }
        }
        return -1;
    }

    // BFS over every cell NOT marked in excluded, starting from start_id,
    // and report whether that BFS reaches every non-excluded cell -- i.e.
    // whether (cells \ excluded) is a single connected piece. This is a
    // much stronger requirement than "goal stays reachable": a manufactured
    // region that merely leaves SOME start-goal route intact can still sit
    // astride the only corridor to a DIFFERENT ring's pocket, silently
    // making that other pocket (and hence the whole recursive cascade)
    // unreachable whenever this region's door is closed (its default
    // state). Requiring the complement to stay fully connected guarantees
    // the region is a genuine dead-end appendage -- closing it off can
    // never cut the rest of the maze off from itself, no matter which
    // other pockets end up assigned elsewhere in that same complement.
    bool region_is_prunable(const vector<char> &excluded) const {
        if (excluded[start_id] || excluded[goal_id]) return false;
        int total_remaining = 0;
        for (int c = 0; c < static_cast<int>(cells.size()); c++) {
            if (!excluded[c]) total_remaining++;
        }
        vector<char> visited(cells.size(), 0);
        queue<int> q;
        visited[start_id] = 1;
        int reached = 1;
        q.push(start_id);
        while (!q.empty()) {
            int u = q.front();
            q.pop();
            for (const Adj &a : graph[u]) {
                if (excluded[a.to] || visited[a.to]) continue;
                visited[a.to] = 1;
                reached++;
                q.push(a.to);
            }
        }
        return visited[goal_id] != 0 && reached == total_remaining;
    }

    // Find dead-end pockets reachable from the start's side via exactly one
    // bridge edge. Pockets whose far side would still leave the goal
    // reachable (side[goal_id]) are dead-end candidates for the
    // binary-counter's recursive "ring" gadgets; pockets are consumed
    // greedily so returned pockets never share a cell.
    //
    // require_tree controls whether a far side containing an internal cycle
    // (a route that could let the hero bypass a prefix of the serial door
    // chain, defeating the recursive AND-condition and collapsing the whole
    // cascade) is skipped outright. This is a tradeoff, not a correctness
    // requirement: evaluate_arrays() always re-verifies the final plan with
    // exact BFS, and main() only ever swaps in a binary-counter plan when it
    // strictly beats the portfolio fallback, so a cyclic pocket can never
    // make output invalid or worse than not using it -- it can only
    // occasionally make ITS OWN chain collapse (e.g. T=85 instead of the
    // intended O(2^L) blowup). require_tree=true avoids that per-plan
    // collapse risk but shrinks the usable pocket pool, which on
    // pocket-scarce mazes caps L (and thus T) far below what the fuller,
    // cyclic-tolerant pool reaches. The caller builds both variants and
    // keeps whichever verifies higher, so neither tradeoff is paid blindly.
    //
    // consumed and used_edge are shared in/out state: every accepted
    // pocket's far-side cells and level edges are marked here so a later
    // call to find_cut_pockets (manufactured pockets, see below) never
    // claims a cell or door already spoken for by a natural pocket.
    vector<Pocket> find_pockets(bool require_tree, vector<char> &consumed, vector<char> &used_edge) const {
        vector<int> tin(cells.size(), -1), low(cells.size(), 0), bridges;
        int dfs_timer = 0;
        bridge_dfs(start_id, -1, tin, low, dfs_timer, bridges);

        // Bridges nest: a small pendant cell's entrance bridge sits inside
        // the far side of a much larger enclosing bridge (bridge_dfs's
        // post-order visits the small/deep one first). Sorting by far-side
        // size descending lets the greedy consumer claim the large
        // enclosing pocket first -- its own BFS-farthest search already
        // reaches every nested cell -- instead of exhausting the budget on
        // tiny 1-cell nested pendants and never reaching the big pocket
        // whose cells they'd overlap.
        vector<pair<int, int>> ranked_bridges; // (far_count, edge_idx)
        for (int edge_idx : bridges) {
            vector<int> side = start_side_without_edge(edge_idx);
            if (!side[goal_id]) continue; // only dead-end bridges: goal must stay reachable without this edge
            int far_count = 0;
            for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                if (!side[c]) far_count++;
            }
            ranked_bridges.push_back({far_count, edge_idx});
        }
        sort(ranked_bridges.rbegin(), ranked_bridges.rend());

        vector<Pocket> pockets;

        for (const auto &entry : ranked_bridges) {
            int edge_idx = entry.second;
            vector<int> side = start_side_without_edge(edge_idx);

            int u = edges[edge_idx].u;
            int v = edges[edge_idx].v;
            int near_cell, far_root;
            if (side[u] && !side[v]) {
                near_cell = u;
                far_root = v;
            } else if (side[v] && !side[u]) {
                near_cell = v;
                far_root = u;
            } else {
                continue;
            }
            if (consumed[near_cell] || consumed[far_root] || used_edge[edge_idx]) continue;

            vector<int> far_cells;
            for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                if (!side[c]) far_cells.push_back(c);
            }
            bool overlap = false;
            for (int c : far_cells) {
                if (consumed[c]) {
                    overlap = true;
                    break;
                }
            }
            if (overlap) continue;

            vector<char> in_far(cells.size(), 0);
            for (int c : far_cells) in_far[c] = 1;
            if (require_tree) {
                // A pure tree has exactly far_count-1 internal edges; extra
                // edges mean a cycle exists. Skip outright (without
                // consuming its cells) so a genuinely tree-shaped bridge
                // nested inside it can still be found separately.
                int internal_edges = 0;
                for (int c : far_cells) {
                    for (const Adj &a : graph[c]) {
                        if (in_far[a.to] && a.to > c) internal_edges++;
                    }
                }
                if (internal_edges != static_cast<int>(far_cells.size()) - 1) continue;
            }

            vector<int> dist(cells.size(), -1), parent_cell(cells.size(), -1), parent_edge(cells.size(), -1);
            queue<int> q;
            dist[far_root] = 0;
            q.push(far_root);
            int farthest = far_root;
            while (!q.empty()) {
                int x = q.front();
                q.pop();
                if (dist[x] > dist[farthest]) farthest = x;
                for (const Adj &a : graph[x]) {
                    if (!in_far[a.to] || dist[a.to] >= 0) continue;
                    dist[a.to] = dist[x] + 1;
                    parent_cell[a.to] = x;
                    parent_edge[a.to] = a.edge;
                    q.push(a.to);
                }
            }

            Pocket p;
            p.level_edges.push_back({edge_idx});
            vector<int> tail_edges;
            int cur = farthest;
            while (cur != far_root) {
                tail_edges.push_back(parent_edge[cur]);
                cur = parent_cell[cur];
            }
            reverse(tail_edges.begin(), tail_edges.end());
            for (int e : tail_edges) p.level_edges.push_back({e});
            p.switch_cell = farthest;

            pockets.push_back(p);
            for (int c : far_cells) consumed[c] = 1;
            used_edge[edge_idx] = 1;
            for (int e : tail_edges) used_edge[e] = 1;
        }
        return pockets;
    }

    // Manufacture pockets out of multi-entrance regions when natural
    // single-bridge dead ends are scarce. Doors of the same type share
    // open/close state, so sealing EVERY entrance edge of a region with
    // that type collapses the whole region into one controllable gate --
    // functionally a single bridge, but built from several physical doors.
    // Concentric BFS "shells" around a chosen core cell give this for free
    // with several nested levels at once: shell d's boundary (edges
    // between distance d-1 and distance d from the core, over the WHOLE
    // graph) is disjoint from every other shell's boundary by construction
    // (adjacent cells' BFS distances differ by at most 1), so assigning
    // shell d's edges the door type for level (usable_levels-d+1)
    // reproduces the serial multi-bit AND-chain build_binary_counter_plan
    // needs -- without requiring the region to be a literal single-file
    // corridor. Growth stops at the first shell that is too wide (over
    // cut_cap edges) or collides with an edge/cell already claimed by an
    // earlier pocket, keeping every shell found before that point. A
    // region is only accepted when start and goal stay connected with the
    // ENTIRE region removed, so the manufactured pocket is a genuine
    // optional detour, never load-bearing for base connectivity. Width/
    // collision growth and depth acceptance are two independent limits: a
    // core picked purely by distance-from-start can grow a wide-enough
    // ball that wraps around and straddles a cut vertex load-bearing for
    // the rest of the maze well before it runs out of width budget, so a
    // single prunability test at the deepest ball reached (recover_shallower
    // =false, the original behavior) throws away the whole core whenever
    // that deepest ball is the one that overreaches, even though a
    // shallower ball from the SAME core -- still deeper than most natural
    // pockets -- would have been perfectly valid (observed to reject ~90%
    // of width-feasible candidates on pocket-scarce mazes this way).
    // recover_shallower=true retries progressively shallower balls from the
    // same core before giving up on it. This is NOT a strict improvement,
    // though: consuming a core's cells at a recovered shallow depth can
    // preempt a DIFFERENT, better-fitting core later in the (dist-from-
    // start-sorted) seed order that the plain skip-and-move-on behavior
    // would have left room for -- greedy consumption is order-sensitive, so
    // more local success does not imply a better global pocket pool. The
    // caller (build_binary_counter_plan / main) must therefore keep BOTH
    // recover_shallower variants in its candidate pool and pick whichever
    // verifies higher, same discipline as require_tree and
    // manufactured_first above, rather than replacing the old behavior
    // outright.
    vector<Pocket> find_cut_pockets(int cut_cap, int max_levels, vector<char> &consumed,
                                    vector<char> &used_edge, bool recover_shallower,
                                    bool wide_seed_cap) const {
        vector<Pocket> result;
        vector<int> dist_start = bfs_cell(start_id);
        vector<int> seeds;
        for (int c = 0; c < static_cast<int>(cells.size()); c++) {
            if (c == start_id || c == goal_id || consumed[c] || dist_start[c] < 0) continue;
            seeds.push_back(c);
        }
        sort(seeds.begin(), seeds.end(), [&](int a, int b) { return dist_start[a] > dist_start[b]; });
        // Every candidate core is cheap (one more BFS over <=400 cells), so
        // trying more cores than the first 90 by distance-from-start costs
        // only microseconds but lets scarce-pocket mazes reach cores a
        // tighter head cut would miss entirely. This is NOT simply "more is
        // better", though (measured directly): which cores succeed and in
        // what order determines what gets marked consumed, so scanning more
        // seeds can make an EARLIER core grab cells a later, better-fitting
        // core in the ORIGINAL 90-cap order would have used instead --
        // several seeds got noticeably worse when the cap was raised
        // unconditionally. wide_seed_cap is therefore a separate candidate
        // dimension, like recover_shallower, not a replacement default.
        int seed_cap = min(static_cast<int>(seeds.size()), wide_seed_cap ? 400 : 90);

        for (int si = 0; si < seed_cap; si++) {
            int core = seeds[si];
            if (consumed[core]) continue;
            vector<int> dist_core = bfs_cell(core);
            int maxd = 0;
            for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                maxd = max(maxd, dist_core[c]);
            }
            int cap_levels = min(max_levels, maxd);
            if (cap_levels < 1) continue;

            vector<vector<int>> shell_cut(cap_levels + 1);
            int usable_levels = 0;
            for (int d = 1; d <= cap_levels; d++) {
                vector<int> cut_edges;
                bool collision = false;
                for (int eidx = 0; eidx < static_cast<int>(edges.size()); eidx++) {
                    int a = edges[eidx].u, b = edges[eidx].v;
                    int da = dist_core[a], db = dist_core[b];
                    bool crosses = (da == d - 1 && db == d) || (da == d && db == d - 1);
                    if (!crosses) continue;
                    if (used_edge[eidx]) {
                        collision = true;
                        break;
                    }
                    cut_edges.push_back(eidx);
                }
                if (collision || cut_edges.empty() || static_cast<int>(cut_edges.size()) > cut_cap) break;
                shell_cut[d] = cut_edges;
                usable_levels = d;
            }
            if (usable_levels < 1) continue;

            // Test prunability starting at the deepest depth reached by
            // shell growth; when recover_shallower is set, keep retrying
            // progressively shallower balls from this same core instead of
            // giving up after the first failure (see class comment above).
            int chosen_levels = 0;
            vector<char> region;
            for (int lv = usable_levels; lv >= 1; lv--) {
                vector<char> cand_region(cells.size(), 0);
                bool bad = false;
                for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                    if (dist_core[c] < 0 || dist_core[c] > lv - 1) continue;
                    if (c == start_id || c == goal_id || consumed[c]) {
                        bad = true;
                        break;
                    }
                    cand_region[c] = 1;
                }
                if (bad || !region_is_prunable(cand_region)) {
                    if (!recover_shallower) break;
                    continue;
                }
                chosen_levels = lv;
                region = std::move(cand_region);
                break;
            }
            if (chosen_levels < 1) continue;
            usable_levels = chosen_levels;

            Pocket p;
            for (int j = 1; j <= usable_levels; j++) {
                p.level_edges.push_back(shell_cut[usable_levels - j + 1]);
            }
            p.switch_cell = core;
            result.push_back(p);

            for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                if (region[c]) consumed[c] = 1;
            }
            for (const auto &lvl : p.level_edges) {
                for (int e : lvl) used_edge[e] = 1;
            }
        }
        return result;
    }

    // Switch-parity binary-counter construction: a recursive "Baguenaudier
    // (Chinese rings)" gadget. Ring 0 is switch 0, freely reachable from
    // the start (the hub). Ring i (i=1..L) lives at the end of a dead-end
    // pocket whose entrance requires bits 0..i-2 all OFF (their default
    // state) AND bit i-1 ON -- i.e. i serial doors, i-1 of type 2j
    // (open-by-default, "OFF check") for j=0..i-2, plus one door of type
    // 2*(i-1)+1 (closed-by-default, "ON check") for the last edge. This
    // recursive AND-condition is exactly the Chinese-rings togglability
    // rule, so reaching ring i's alcove for the first time forces
    // toggling every lower ring O(2^i) times: pressing switch i-1 to
    // satisfy ring i's ON-check requires first satisfying ring (i-1)'s own
    // entrance (recursively), and once bit i-1 is set it must later be
    // reset to reach ring (i+1), so each level roughly doubles the total
    // forced corridor round trips. Each pocket is a genuine dead-end tree
    // (see find_pockets), so no alternate route can bypass its doors. A
    // final gate on a genuine start-goal essential bridge (type
    // 2*L+1, needs bit L ON) blocks the goal until ring L has been
    // reached at least once, forcing the whole recursive cascade before
    // the goal becomes reachable at all.
    //
    // Every accepted ring/pocket is a real graph bridge (or an edge inside
    // a verified cycle-free pocket), so this never creates a false
    // bottleneck; the caller must still gate acceptance on evaluate_arrays
    // returning a value that beats the fallback, since maze structure
    // (few or short pockets, no essential bridge) can make this a no-op.
    // manufactured_first controls search order between the two pocket
    // finders. Natural pockets are free-riding graph bridges, but there can
    // be dozens of tiny ones (length 1-2) scattered through the same open
    // area a good manufactured "onion" core would otherwise grow through;
    // claiming those first can fragment the territory and starve a
    // potentially deep manufactured chain down to 2-3 levels even though
    // its true local geometry (checked in isolation) supports far more.
    // Running manufactured search first lets a few large cores claim their
    // territory before the swarm of small natural pockets nibbles at it;
    // running natural first is cheaper (1 door/level) when natural pockets
    // already cover the ladder well. Neither order dominates, so the
    // caller builds both and keeps whichever verifies higher.
    //
    // use_manufactured lets the caller ALSO try a natural-pockets-only pool
    // (find_cut_pockets skipped entirely). Merging manufactured pockets in
    // is not always a strict improvement over natural-only, even though
    // every individual manufactured pocket is independently a genuine
    // dead-end appendage (region_is_prunable) and the whole plan is always
    // exact-BFS reverified: the greedy length-ladder assignment picks the
    // cheapest sufficient pocket per slot, so a manufactured pocket can
    // still occupy a slot a natural pocket would have filled (same level
    // count, lower sort key by pure chance of insertion order), or its
    // extra per-level door cost can exhaust the M budget earlier and cap L
    // below what natural-only reaches on mazes where natural coverage was
    // already good. On mazes where natural pockets are scarce, merging
    // manufactured in is what raises the floor. Neither pool dominates, so
    // the caller builds both and keeps whichever verifies higher -- same
    // discipline as require_tree and manufactured_first above.
    //
    // recover_shallower and wide_seed_cap are forwarded to find_cut_pockets
    // (only meaningful when use_manufactured is set): see its comments for
    // why both are pool-composition tradeoffs, not strict wins, and must
    // stay separate candidates rather than replacing the original behavior.
    //
    // favor_deep_low_ranks changes WHICH pocket serves each rank without
    // changing L or the door budget spent. The greedy ladder assignment
    // below picks, for each rank r in increasing order, the SHALLOWEST
    // pocket deep enough to serve it -- optimal for maximizing L, since for
    // natural pockets the door cost of serving rank r is exactly r
    // regardless of which qualifying pocket is chosen (only the first r of
    // its levels ever get doors). That leaves any EXCESS depth of the
    // chosen pocket as a free bonus corridor beyond the last gated door
    // (switch_cell sits at the pocket's true bottom, not at level r), but
    // ascending assignment systematically routes that bonus to whichever
    // pocket happens to be barely-sufficient for its rank -- usually near
    // zero bonus -- while any deeper pockets in the pool that were not
    // load-bearing for feasibility go completely unused. Since ring 1 is
    // toggled roughly twice as often as ring 2, four times as often as
    // ring 3, and so on (the Chinese-rings recursion), the corridor length
    // of whichever pocket serves rank 1 dominates the final T far more than
    // any other rank's. favor_deep_low_ranks reassigns the SAME feasible L
    // to a different pocket per rank: ranks L..2 are still matched
    // smallest-sufficient-first (in descending order, an equally valid
    // maximum-matching greedy for this nested-compatibility structure, so L
    // is unaffected), but whatever is left over after that -- specifically
    // including any pocket whose depth was never actually needed for
    // feasibility -- is handed to rank 1 by taking the DEEPEST remaining
    // pocket, at the same door cost (1 door) as the shallowest one would
    // have cost. This can only help: it is computed as an alternate
    // candidate and only swapped in when it does not increase total door
    // cost past M and does not fail to cover every rank (both checked
    // explicitly), so a maze with no exploitable slack silently falls back
    // to the original assignment.
    //
    // use_permanent_walls sacrifices ONE otherwise-unused switch type
    // (index k-1) entirely: no switch of that type is ever placed, so any
    // door of type 2*(k-1)+1 (closed-by-default) stays closed forever, a
    // permanent wall costing only door budget, not a switch slot. The ring
    // cascade is capped to at most k-2 levels to guarantee that reservation
    // never collides with a cascade door type. Whatever door budget is left
    // after the (now slightly shorter) cascade and the final gate is spent
    // reshaping the "hub" -- every near-side cell not already claimed by a
    // ring pocket -- into a long serpentine corridor via
    // add_permanent_walls, so every physical step through the shared hub
    // area (crossed on every single round trip, regardless of which ring)
    // costs as many moves as the leftover door budget can force. This
    // trades one ring level (halving the round-trip COUNT) for a
    // potentially much longer per-trip distance; neither dominates the
    // other in general, so it must stay a separate candidate the caller
    // compares by exact-BFS T like every other variant here.
    Plan build_binary_counter_plan(int &result_t, bool require_tree, bool manufactured_first,
                                    bool use_manufactured, bool recover_shallower, bool wide_seed_cap,
                                    bool favor_deep_low_ranks, bool use_permanent_walls) {
        Plan plan;
        result_t = -1;
        int max_rank = use_permanent_walls ? (k - 2) : (k - 1);
        if (max_rank < 1) return plan; // no room for even one ring once a switch type is sacrificed

        // Pick the final gate FIRST, before any pocket search. It must be a
        // graph bridge that's essential for start-goal connectivity (goal
        // unreachable without it), and among those we pick the one closest
        // to goal (smallest goal-side component): that maximizes how much
        // of the maze sits on the start side, available to pockets. This
        // ordering matters for correctness, not just yield -- a pocket that
        // hangs off the backbone on the FAR side of whichever essential
        // bridge gets gated last would need bit L set to reach it, but bit
        // L only gets set by visiting ring L's own pocket, so such a pocket
        // would be permanently unreachable (a silent deadlock, not merely a
        // shorter chain). Restricting every pocket search below to cells
        // reachable from start without crossing final_edge rules that out
        // entirely, regardless of which essential bridge ends up chosen.
        vector<int> tin(cells.size(), -1), low(cells.size(), 0), bridges;
        int dfs_timer = 0;
        bridge_dfs(start_id, -1, tin, low, dfs_timer, bridges);
        int final_edge = -1;
        int best_near_count = -1;
        for (int edge_idx : bridges) {
            vector<int> side = start_side_without_edge(edge_idx);
            if (side[goal_id]) continue; // need an essential bridge: goal unreachable without it
            int near_count = 0;
            for (int c = 0; c < static_cast<int>(cells.size()); c++) {
                if (side[c]) near_count++;
            }
            if (near_count > best_near_count) {
                best_near_count = near_count;
                final_edge = edge_idx;
            }
        }
        if (final_edge < 0) return plan; // no true bottleneck to gate; this construction cannot apply

        vector<int> final_near_side = start_side_without_edge(final_edge);

        // Natural pockets first (cheapest: exactly 1 door per level), then
        // fill gaps in the length ladder with manufactured pockets carved
        // out of multi-entrance regions -- see find_cut_pockets. Both
        // finders share consumed/used_edge state so no cell or door is
        // ever claimed twice across the combined pool. Cells beyond
        // final_edge are pre-marked consumed so neither finder can ever
        // reach across it.
        vector<char> consumed(cells.size(), 0);
        vector<char> used_edge(edges.size(), 0);
        for (int c = 0; c < static_cast<int>(cells.size()); c++) {
            if (!final_near_side[c]) consumed[c] = 1;
        }
        vector<Pocket> pockets;
        vector<Pocket> manufactured;
        if (!use_manufactured) {
            pockets = find_pockets(require_tree, consumed, used_edge);
        } else if (manufactured_first) {
            manufactured = find_cut_pockets(6, k - 1, consumed, used_edge, recover_shallower, wide_seed_cap);
            pockets = find_pockets(require_tree, consumed, used_edge);
        } else {
            pockets = find_pockets(require_tree, consumed, used_edge);
            manufactured = find_cut_pockets(6, k - 1, consumed, used_edge, recover_shallower, wide_seed_cap);
        }
        pockets.insert(pockets.end(), manufactured.begin(), manufactured.end());
        if (pockets.empty()) return plan;

        // Sort by length first (how many ring slots a pocket can possibly
        // serve), then by total door cost so a cheap natural pocket is
        // preferred over a same-length manufactured one that needs more
        // physical doors for the same number of levels.
        sort(pockets.begin(), pockets.end(), [](const Pocket &a, const Pocket &b) {
            if (a.level_edges.size() != b.level_edges.size()) return a.level_edges.size() < b.level_edges.size();
            int cost_a = 0, cost_b = 0;
            for (const auto &lvl : a.level_edges) cost_a += static_cast<int>(lvl.size());
            for (const auto &lvl : b.level_edges) cost_b += static_cast<int>(lvl.size());
            return cost_a < cost_b;
        });

        // Greedily match the smallest pocket long enough for slot r=1,2,3,...
        // This is the standard interval-scheduling greedy for maximizing
        // the number of consecutive slots covered, which maximizes L (and
        // therefore the 2^L blowup) -- the dominant factor in the final T.
        vector<int> assigned;
        size_t ptr = 0;
        int running_doors = 0;
        int r = 1;
        while (r <= max_rank) {
            while (ptr < pockets.size() && static_cast<int>(pockets[ptr].level_edges.size()) < r) ptr++;
            if (ptr >= pockets.size()) break;
            int cost = 0;
            for (int j = 0; j < r; j++) cost += static_cast<int>(pockets[ptr].level_edges[j].size());
            if (running_doors + cost + 1 > m) break; // keep 1 door reserved for the final gate
            assigned.push_back(static_cast<int>(ptr));
            running_doors += cost;
            ptr++;
            r++;
        }
        int L = static_cast<int>(assigned.size());
        if (L == 0) return plan;

        if (favor_deep_low_ranks) {
            // Re-derive the same L ranks from the full pool (not just the
            // pockets the ascending pass happened to touch), matching
            // ranks L..2 smallest-sufficient-first from the remaining pool
            // (an equally valid maximum-matching greedy for this nested
            // depth>=rank compatibility structure, so it cannot reduce
            // feasibility below L), then handing rank 1 -- toggled far more
            // often than any other rank -- whatever pocket is deepest among
            // what is left over, at the same 1-door cost any other
            // depth>=1 pocket would have cost. Swapped in only if it still
            // covers all L ranks within the door budget; otherwise the
            // original ascending assignment above is kept untouched.
            vector<int> alt_assigned(L, -1);
            vector<char> used(pockets.size(), 0);
            bool ok = true;
            int alt_running_doors = 0;
            for (int rank = L; rank >= 2 && ok; rank--) {
                int best_idx = -1;
                for (int idx = 0; idx < static_cast<int>(pockets.size()); idx++) {
                    if (used[idx] || static_cast<int>(pockets[idx].level_edges.size()) < rank) continue;
                    if (best_idx < 0 || pockets[idx].level_edges.size() < pockets[best_idx].level_edges.size()) {
                        best_idx = idx;
                    }
                }
                if (best_idx < 0) {
                    ok = false;
                    break;
                }
                used[best_idx] = 1;
                alt_assigned[rank - 1] = best_idx;
                for (int j = 0; j < rank; j++) alt_running_doors += static_cast<int>(pockets[best_idx].level_edges[j].size());
            }
            if (ok) {
                int best_idx = -1;
                for (int idx = 0; idx < static_cast<int>(pockets.size()); idx++) {
                    if (used[idx] || pockets[idx].level_edges.empty()) continue;
                    if (best_idx < 0 || pockets[idx].level_edges.size() > pockets[best_idx].level_edges.size()) {
                        best_idx = idx;
                    }
                }
                if (best_idx < 0) {
                    ok = false;
                } else {
                    alt_assigned[0] = best_idx;
                    alt_running_doors += static_cast<int>(pockets[best_idx].level_edges[0].size());
                }
            }
            if (ok && alt_running_doors + 1 <= m) {
                assigned = std::move(alt_assigned);
                running_doors = alt_running_doors;
            }
        }

        plan.switch_cell_type.push_back({start_id, 0});
        for (int i = 1; i <= L; i++) {
            const Pocket &p = pockets[assigned[i - 1]];
            for (int j = 0; j < i; j++) {
                int door_type = (j < i - 1) ? (2 * j) : (2 * (i - 1) + 1);
                for (int e : p.level_edges[j]) {
                    plan.door_edge_type.push_back({e, door_type});
                }
            }
            plan.switch_cell_type.push_back({p.switch_cell, i});
        }
        plan.door_edge_type.push_back({final_edge, 2 * L + 1});

        if (use_permanent_walls) {
            int wall_budget = m - static_cast<int>(plan.door_edge_type.size());
            add_permanent_walls(plan, wall_budget, k - 1, final_near_side, consumed);
        }

        vector<int> door_type(edges.size(), -1);
        vector<int> switch_type(cells.size(), -1);
        for (const auto &entry : plan.door_edge_type) door_type[entry.first] = entry.second;
        for (const auto &entry : plan.switch_cell_type) switch_type[entry.first] = entry.second;
        result_t = evaluate_arrays(door_type, switch_type);
        return plan;
    }

    // Reshape the "hub" -- every near-side cell not already claimed by a
    // ring pocket -- into a long serpentine corridor using wall_type, a
    // switch type the caller guarantees is never assigned to any switch
    // (so its closed-by-default door type, 2*wall_type+1, stays closed
    // forever; unlimited edges may share it since it costs door budget,
    // not a switch slot). A DFS spanning tree over the hub's currently-
    // free (undoored) edges is exactly the classic "recursive backtracker"
    // maze generator: it winds through every hub cell along a single long
    // path, so the tree alone already connects the whole hub, and every
    // non-tree "chord" edge is a shortcut that makes the hub's actual
    // shortest paths shorter than that winding tour. Removing a chord can
    // therefore never disconnect the hub -- the tree's connectivity does
    // not depend on which chords remain -- so up to wall_budget chords are
    // sealed permanently, nearest to the start first (by plain BFS
    // distance, ignoring every door), since that is the region every
    // forced round trip re-crosses regardless of which ring it is bound
    // for.
    void add_permanent_walls(Plan &plan, int wall_budget, int wall_type, const vector<int> &final_near_side,
                             const vector<char> &consumed) {
        if (wall_budget <= 0) return;
        vector<char> door_used(edges.size(), 0);
        for (const auto &entry : plan.door_edge_type) door_used[entry.first] = 1;

        vector<vector<pair<int, int>>> hub_adj(cells.size());
        vector<int> eligible;
        for (int eidx = 0; eidx < static_cast<int>(edges.size()); eidx++) {
            if (door_used[eidx]) continue;
            int u = edges[eidx].u, v = edges[eidx].v;
            if (!final_near_side[u] || !final_near_side[v]) continue;
            if (consumed[u] || consumed[v]) continue;
            hub_adj[u].push_back({v, eidx});
            hub_adj[v].push_back({u, eidx});
            eligible.push_back(eidx);
        }
        if (eligible.empty()) return;

        // Shuffle neighbor order per cell so the backtracker winds instead
        // of always following the same axis-aligned bias.
        for (auto &adj : hub_adj) {
            for (int i = static_cast<int>(adj.size()) - 1; i > 0; i--) {
                int j = rng.next_int(0, i);
                swap(adj[i], adj[j]);
            }
        }

        vector<char> visited(cells.size(), 0);
        vector<char> tree_edge(edges.size(), 0);
        vector<pair<int, size_t>> stack;
        visited[start_id] = 1;
        stack.push_back({start_id, 0});
        while (!stack.empty()) {
            int u = stack.back().first;
            size_t &i = stack.back().second;
            if (i >= hub_adj[u].size()) {
                stack.pop_back();
                continue;
            }
            int v = hub_adj[u][i].first;
            int eidx = hub_adj[u][i].second;
            i++;
            if (visited[v]) continue;
            visited[v] = 1;
            tree_edge[eidx] = 1;
            stack.push_back({v, 0});
        }

        vector<int> dist_start = bfs_cell(start_id);
        vector<int> chords;
        for (int eidx : eligible) {
            if (!tree_edge[eidx]) chords.push_back(eidx);
        }
        sort(chords.begin(), chords.end(), [&](int a, int b) {
            int da = min(dist_start[edges[a].u], dist_start[edges[a].v]);
            int db = min(dist_start[edges[b].u], dist_start[edges[b].v]);
            if (da != db) return da < db;
            return a < b;
        });

        int wall_door_type = 2 * wall_type + 1;
        int sealed = 0;
        for (int eidx : chords) {
            if (sealed >= wall_budget) break;
            plan.door_edge_type.push_back({eidx, wall_door_type});
            sealed++;
        }
    }

    void print_plan(const Plan &plan) const {
        cout << plan.door_edge_type.size() << '\n';
        for (const auto &entry : plan.door_edge_type) {
            const Edge &e = edges[entry.first];
            cout << e.d << ' ' << e.i << ' ' << e.j << ' ' << entry.second << '\n';
        }
        cout << plan.switch_cell_type.size() << '\n';
        for (const auto &entry : plan.switch_cell_type) {
            auto [i, j] = cells[entry.first];
            cout << i << ' ' << j << ' ' << entry.second << '\n';
        }
    }

    int door_count_except(const vector<Gate> &solution, int replace_idx) const {
        int total = 0;
        for (int i = 0; i < static_cast<int>(solution.size()); i++) {
            if (i == replace_idx) continue;
            total += static_cast<int>(solution[i].edges.size());
        }
        return total;
    }

    bool conflicts_after_replace(const vector<Gate> &solution, int replace_idx, const Gate &gate) const {
        if (gate.edges.empty() || door_count_except(solution, replace_idx) + static_cast<int>(gate.edges.size()) > m) {
            return true;
        }
        for (int i = 0; i < static_cast<int>(solution.size()); i++) {
            if (i == replace_idx) continue;
            if (solution[i].sw == gate.sw) return true;
            for (int edge_idx : gate.edges) {
                for (int used_edge : solution[i].edges) {
                    if (edge_idx == used_edge) return true;
                }
            }
        }
        return false;
    }

    vector<Gate> construct(const vector<Candidate> &candidates, int &best_t) {
        vector<Gate> current;
        vector<int> used_edge(edges.size(), 0), used_switch(cells.size(), 0);
        best_t = evaluate(current);
        int used_doors = 0;

        for (int type = 0; type < k && used_doors < m; type++) {
            int selected_t = best_t;
            Gate selected{{}, -1};
            for (const Candidate &cand : candidates) {
                if (used_switch[cand.sw] || used_doors + static_cast<int>(cand.edges.size()) > m) continue;
                bool any_used = false;
                for (int edge_idx : cand.edges) {
                    if (used_edge[edge_idx]) {
                        any_used = true;
                        break;
                    }
                }
                if (any_used) continue;
                vector<Gate> trial = current;
                trial.push_back({cand.edges, cand.sw});
                int t = evaluate(trial);
                if (t > selected_t) {
                    selected_t = t;
                    selected = {cand.edges, cand.sw};
                }
            }
            if (selected.edges.empty()) break;
            current.push_back(selected);
            for (int edge_idx : selected.edges) {
                used_edge[edge_idx] = 1;
            }
            used_switch[selected.sw] = 1;
            used_doors += static_cast<int>(selected.edges.size());
            best_t = selected_t;
        }
        return current;
    }

    vector<Gate> improve(vector<Gate> current, const vector<Candidate> &candidates, int current_t,
                         const Timer &timer, int &best_t, int &iterations, int &accepted,
                         double hillclimb_limit, double anneal_limit) {
        vector<Gate> best = current;
        if (current_t > best_t) {
            best_t = current_t;
        }
        if (current.empty() || candidates.empty()) return best;

        static const double START_TEMP = param_double("AHC_PARAM_START_TEMP", 6.235);
        static const double END_TEMP = param_double("AHC_PARAM_END_TEMP", 0.823);

        bool improved = true;
        while (improved && timer.elapsed() < hillclimb_limit) {
            improved = false;
            for (int idx = 0; idx < static_cast<int>(current.size()) && timer.elapsed() < hillclimb_limit; idx++) {
                Gate old_gate = current[idx];
                Gate local_best_gate = old_gate;
                int local_best_t = current_t;
                for (const Candidate &cand : candidates) {
                    if (timer.elapsed() >= hillclimb_limit) break;
                    Gate next_gate{cand.edges, cand.sw};
                    if (conflicts_after_replace(current, idx, next_gate)) continue;
                    current[idx] = next_gate;
                    iterations++;
                    int next_t = evaluate(current);
                    if (next_t > local_best_t) {
                        local_best_t = next_t;
                        local_best_gate = next_gate;
                    }
                }
                current[idx] = local_best_gate;
                if (local_best_t > current_t) {
                    current_t = local_best_t;
                    accepted++;
                    improved = true;
                    if (current_t > best_t) {
                        best_t = current_t;
                        best = current;
                    }
                } else {
                    current[idx] = old_gate;
                }
            }
        }

        while (timer.elapsed() < anneal_limit) {
            iterations++;
            int idx = rng.next_int(0, static_cast<int>(current.size()) - 1);
            const Candidate &cand = candidates[rng.next_int(0, static_cast<int>(candidates.size()) - 1)];
            Gate next_gate{cand.edges, cand.sw};
            if (conflicts_after_replace(current, idx, next_gate)) continue;
            Gate old_gate = current[idx];
            current[idx] = next_gate;
            int next_t = evaluate(current);
            int diff = next_t - current_t;
            double progress = min(1.0, timer.elapsed() / anneal_limit);
            double temp = START_TEMP * pow(END_TEMP / START_TEMP, progress);
            bool take = diff >= 0;
            if (!take && next_t >= 0) {
                take = exp(static_cast<double>(diff) / temp) > rng.next_double();
            }
            if (take) {
                current_t = next_t;
                accepted++;
                if (current_t > best_t) {
                    best_t = current_t;
                    best = current;
                }
            } else {
                current[idx] = old_gate;
            }
        }
        return best;
    }

    vector<Gate> best_single_candidate(const vector<Candidate> &candidates, const Timer &timer,
                                       double time_limit, int &best_t, int &iterations) {
        vector<Gate> best;
        for (const Candidate &cand : candidates) {
            if (timer.elapsed() >= time_limit) break;
            if (static_cast<int>(cand.edges.size()) > m) continue;
            vector<Gate> trial{{cand.edges, cand.sw}};
            iterations++;
            int t = evaluate(trial);
            if (t > best_t) {
                best_t = t;
                best = trial;
            }
        }
        return best;
    }

    // Composite layer-cut portfolio: repeatedly stack additional gates (drawn
    // from a combined bridge + layer-cut pool) on top of the current best
    // solution, filling toward the full K switch budget instead of stopping
    // after a single appended gate. Falls back to a from-scratch pair search
    // when stacking made little progress (e.g. no usable base solution).
    // Every trial is gated by exact BFS via evaluate(), and a result is
    // returned only when it strictly beats the incoming best_t, so output
    // never worsens.
    vector<Gate> composite_layer_search(const vector<Gate> &base, const vector<Candidate> &candidates,
                                        const Timer &timer, double time_limit, int &best_t,
                                        int &iterations) {
        vector<Gate> current = base;
        int current_t = best_t;
        vector<Gate> best = current;

        // A) Greedily append the single best candidate gate, repeating until
        // the K switch slots are full, no candidate helps anymore, or the
        // time budget for this phase runs out. This fills closer to the full
        // switch budget instead of stopping at base+1.
        bool progressed = true;
        while (progressed && static_cast<int>(current.size()) < k && timer.elapsed() < time_limit) {
            progressed = false;
            Gate best_gate{{}, -1};
            int best_gate_t = current_t;
            for (const Candidate &cand : candidates) {
                if (timer.elapsed() >= time_limit) break;
                Gate gate{cand.edges, cand.sw};
                if (conflicts_after_replace(current, -1, gate)) continue;
                vector<Gate> trial = current;
                trial.push_back(gate);
                iterations++;
                int t = evaluate(trial);
                if (t > best_gate_t) {
                    best_gate_t = t;
                    best_gate = gate;
                }
            }
            if (!best_gate.edges.empty()) {
                current.push_back(best_gate);
                current_t = best_gate_t;
                progressed = true;
                if (current_t > best_t) {
                    best_t = current_t;
                    best = current;
                }
            }
        }

        // B) If stacking made little progress (e.g. there was no usable base
        // solution to build on), also try pairs of distinct candidates built
        // from scratch, in case a fresh pair beats the greedy stack above.
        // The outer index is capped to the highest-priority candidates to
        // bound the work.
        if (static_cast<int>(current.size()) <= 2) {
            const int outer_limit = min(static_cast<int>(candidates.size()), 16);
            for (int a = 0; a < outer_limit && timer.elapsed() < time_limit; a++) {
                const Candidate &ca = candidates[a];
                for (int b = a + 1; b < static_cast<int>(candidates.size()); b++) {
                    if (timer.elapsed() >= time_limit) break;
                    const Candidate &cb = candidates[b];
                    if (ca.sw == cb.sw) continue;
                    if (static_cast<int>(ca.edges.size() + cb.edges.size()) > m) continue;
                    bool overlap = false;
                    for (int e1 : ca.edges) {
                        for (int e2 : cb.edges) {
                            if (e1 == e2) {
                                overlap = true;
                                break;
                            }
                        }
                        if (overlap) break;
                    }
                    if (overlap) continue;
                    vector<Gate> trial{{ca.edges, ca.sw}, {cb.edges, cb.sw}};
                    iterations++;
                    int t = evaluate(trial);
                    if (t > best_t) {
                        best_t = t;
                        best = trial;
                    }
                }
            }
        }
        return best;
    }

    vector<Gate> solve(int &baseline_t, int &best_t, int &candidate_count, int &iterations, int &accepted) {
        Timer timer(1.90);
        const double t_hill1 = param_double("AHC_PARAM_T_HILL1", 0.555);
        const double t_anneal1 = param_double("AHC_PARAM_T_ANNEAL1", 0.872);
        const double t_hill2 = param_double("AHC_PARAM_T_HILL2", 1.073);
        const double t_anneal2 = param_double("AHC_PARAM_T_ANNEAL2", 1.567);
        const double t_single = param_double("AHC_PARAM_T_SINGLE", 1.511);
        const double t_composite = param_double("AHC_PARAM_T_COMPOSITE", 1.676);
        const int bridge_cap2 = param_int("AHC_PARAM_BRIDGE_CAP2", 129);
        const int layer_cap = param_int("AHC_PARAM_LAYER_CAP", 188);
        const int combined_cap = param_int("AHC_PARAM_COMBINED_CAP", 379);
        vector<Candidate> core_candidates = make_bridge_candidates(4, 180);
        candidate_count = static_cast<int>(core_candidates.size());
        vector<Gate> empty;
        baseline_t = evaluate(empty);
        best_t = baseline_t;
        iterations = 0;
        accepted = 0;
        vector<Gate> solution;
        vector<Candidate> broad_candidates;
        if (!core_candidates.empty()) {
            solution = construct(core_candidates, best_t);
            if (!solution.empty()) {
                solution = improve(solution, core_candidates, best_t, timer, best_t, iterations, accepted, t_hill1, t_anneal1);

                broad_candidates = make_bridge_candidates(6, bridge_cap2);
                candidate_count = max(candidate_count, static_cast<int>(broad_candidates.size()));
                solution = improve(solution, broad_candidates, best_t, timer, best_t, iterations, accepted, t_hill2, t_anneal2);
            }
        }

        vector<Candidate> layer_candidates = make_layer_cut_candidates(4, layer_cap);
        candidate_count = max(candidate_count, static_cast<int>(broad_candidates.size() + layer_candidates.size()));
        if (layer_candidates.empty()) return solution;

        vector<Gate> layer_single = best_single_candidate(layer_candidates, timer, t_single, best_t, iterations);
        if (!layer_single.empty()) {
            solution = layer_single;
        }

        // Pool bridge and layer-cut candidates together so stacking can pick
        // whichever gate type helps most at each remaining switch slot.
        vector<Candidate> combined_candidates = broad_candidates;
        combined_candidates.insert(combined_candidates.end(), layer_candidates.begin(), layer_candidates.end());
        sort_and_trim_candidates(combined_candidates, combined_cap);

        vector<Gate> composite = composite_layer_search(solution, combined_candidates, timer, t_composite,
                                                        best_t, iterations);
        if (!composite.empty()) {
            solution = composite;
        }

        // Spend the remaining time budget polishing the stacked solution with
        // swap-based hillclimb/anneal over the combined pool, instead of
        // leaving it unused.
        solution = improve(solution, combined_candidates, best_t, timer, best_t, iterations, accepted,
                           t_composite, param_double("AHC_PARAM_T_FINAL", 1.80));
        return solution;
    }

    void print_solution(const vector<Gate> &solution) const {
        int door_total = 0;
        for (const Gate &gate : solution) {
            door_total += static_cast<int>(gate.edges.size());
        }
        cout << door_total << '\n';
        for (int idx = 0; idx < static_cast<int>(solution.size()); idx++) {
            for (int edge_idx : solution[idx].edges) {
                const Edge &e = edges[edge_idx];
                cout << e.d << ' ' << e.i << ' ' << e.j << ' ' << (2 * idx + 1) << '\n';
            }
        }
        cout << solution.size() << '\n';
        for (int idx = 0; idx < static_cast<int>(solution.size()); idx++) {
            auto [i, j] = cells[solution[idx].sw];
            cout << i << ' ' << j << ' ' << idx << '\n';
        }
    }
};

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    string first_line;
    if (!getline(cin, first_line)) {
        return 0;
    }
    stringstream ss(first_line);
    vector<int> header;
    int value;
    while (ss >> value) {
        header.push_back(value);
    }

    if (header.size() == 3) {
        // Whole-program deadline: the binary-counter variant loop below has
        // no internal time bound and runs after solve() has already spent
        // its ~1.88s budget. One case over the 2s limit fails the entire
        // submission, so remaining variants are skipped once this expires.
        Timer total_timer(1.94);
        int n = header[0];
        int m = header[1];
        int k = header[2];
        vector<string> grid(n);
        for (int i = 0; i < n; i++) {
            cin >> grid[i];
        }
        AHC067Solver solver(n, m, k, grid);
        int baseline_t = 0;
        int best_t = 0;
        int candidate_count = 0;
        int iterations = 0;
        int accepted = 0;
        vector<AHC067Solver::Gate> solution =
            solver.solve(baseline_t, best_t, candidate_count, iterations, accepted);

        // Switch-parity binary-counter fallback candidate: a recursive
        // dead-end-pocket gadget that can force order-of-magnitude more
        // forced corridor round trips than the additive gate portfolio
        // above, when the maze has enough dead-end structure for it. Built
        // in up to ten variants -- require_tree x {natural-only,
        // manufactured_first, manufactured_last} x recover_shallower --
        // since none dominates the other across mazes: require_tree=true is
        // immune to a cyclic pocket collapsing its own chain but shrinks the
        // natural pool; merging manufactured pockets in raises the floor on
        // mazes where natural pockets are scarce, but on mazes where
        // natural pockets already cover the length ladder well, a
        // manufactured pocket can still steal a slot the greedy assignment
        // would otherwise give a cheaper natural one (same level count,
        // picked by insertion order) or exhaust the M door budget earlier,
        // capping L below what natural-only reaches -- so natural-only must
        // stay in the candidate pool, not just be a special case of the
        // merged pool. recover_shallower retries a shallower ball per
        // manufactured core when the deepest one fails prunability, and
        // wide_seed_cap tries every eligible cell as a manufacturing core
        // instead of only the 90 farthest from start; both recover pockets
        // on scarce mazes but reorder which cells get consumed, so each can
        // also displace a better core found elsewhere in seed order --
        // measured directly (several seeds got WORSE when either was made
        // the unconditional default). Both false/true pairs must stay
        // available as separate candidates rather than one replacing the
        // other. All variants are exact-BFS verified and only the
        // strictly-better-than-portfolio result (if any) is used, so output
        // quality never regresses; unreachable (-1) or absent-structure
        // (empty plan, best_t stays -1) results are naturally rejected by
        // the comparison.
        //
        // Two more independent dimensions multiply the per-round-trip
        // corridor length instead of just the ring count: favor_deep_low_
        // ranks reassigns which pocket serves which rank (same L, same
        // door cost) so the most-frequently-toggled low ranks get whatever
        // deep pockets the pool has to spare; use_permanent_walls
        // sacrifices one switch type for unlimited permanent-wall doors
        // that reshape the shared hub into a long serpentine corridor,
        // trading one ring level for a much longer per-trip walk. Neither
        // is a strict win over the original ladder (see
        // build_binary_counter_plan's comments), so both stay opt-in
        // candidates. They are placed as the OUTERMOST loops, true first,
        // so the most promising combination gets first crack at the
        // limited remaining time budget under total_timer -- solve() above
        // already spends most of the 1.94s deadline, so later variants in
        // iteration order may never run at all on a slow case.
        int binary_t = -1;
        AHC067Solver::Plan best_binary_plan;
        for (bool use_permanent_walls : {true, false}) {
            for (bool favor_deep_low_ranks : {true, false}) {
                for (bool require_tree : {true, false}) {
                    for (bool use_manufactured : {false, true}) {
                        for (bool manufactured_first : {false, true}) {
                            for (bool recover_shallower : {false, true}) {
                                for (bool wide_seed_cap : {false, true}) {
                                    if (total_timer.expired()) goto variants_done;
                                    int t = -1;
                                    AHC067Solver::Plan p = solver.build_binary_counter_plan(
                                        t, require_tree, manufactured_first, use_manufactured, recover_shallower,
                                        wide_seed_cap, favor_deep_low_ranks, use_permanent_walls);
                                    if (t > binary_t) {
                                        binary_t = t;
                                        best_binary_plan = std::move(p);
                                    }
                                    if (!use_manufactured) break; // no-op without manufactured pockets
                                }
                                if (!use_manufactured) break; // recover_shallower is a no-op without manufactured pockets
                            }
                            if (!use_manufactured) break; // manufactured_first is a no-op without manufactured pockets
                        }
                    }
                }
            }
        }
    variants_done:;
        AHC067Solver::Plan *binary_plan = &best_binary_plan;

        if (binary_t > best_t) {
            solver.print_plan(*binary_plan);
            cerr << "ahc067 baseline_t=" << baseline_t << " best_t=" << binary_t
                 << " gates=" << binary_plan->switch_cell_type.size() << " candidates=" << candidate_count
                 << " iterations=" << iterations << " accepted=" << accepted << " source=binary_counter\n";
        } else {
            solver.print_solution(solution);
            cerr << "ahc067 baseline_t=" << baseline_t << " best_t=" << best_t
                 << " gates=" << solution.size() << " candidates=" << candidate_count
                 << " iterations=" << iterations << " accepted=" << accepted << " source=portfolio\n";
        }
        return 0;
    }

    Timer timer(4.95);
    XorShift64 rng(123456789);

    if (header.size() != 1) {
        return 0;
    }
    int n = header[0];
    vector<int> x(n), y(n), r(n);
    for (int i = 0; i < n; i++) {
        cin >> x[i] >> y[i] >> r[i];
    }

    // Valid AHC001 baseline: give every requested point a 1x1 rectangle.
    // Points are unique, so these rectangles have positive area and never overlap.
    for (int i = 0; i < n; i++) {
        cout << x[i] << ' ' << y[i] << ' ' << x[i] + 1 << ' ' << y[i] + 1 << '\n';
    }

    (void)timer;
    (void)rng;
    return 0;
}
