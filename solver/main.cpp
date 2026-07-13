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

    // A dead-end pocket hanging off a single entrance bridge, verified to
    // be an internally cycle-free (tree) region so no edge inside it can
    // be bypassed by an alternate route. path_edges[0] is the entrance
    // bridge; path_cells[i] is the cell reached after crossing
    // path_edges[i]. Both are ordered from the hub outward to the
    // farthest reachable cell (found via BFS-farthest inside the tree).
    struct Pocket {
        vector<int> path_edges;
        vector<int> path_cells;
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
    vector<Pocket> find_pockets(bool require_tree) const {
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

        vector<char> consumed(cells.size(), 0);
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
            if (consumed[near_cell] || consumed[far_root]) continue;

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
            p.path_edges.push_back(edge_idx);
            p.path_cells.push_back(far_root);
            vector<int> tail_edges, tail_cells;
            int cur = farthest;
            while (cur != far_root) {
                tail_edges.push_back(parent_edge[cur]);
                tail_cells.push_back(cur);
                cur = parent_cell[cur];
            }
            reverse(tail_edges.begin(), tail_edges.end());
            reverse(tail_cells.begin(), tail_cells.end());
            for (size_t idx = 0; idx < tail_edges.size(); idx++) {
                p.path_edges.push_back(tail_edges[idx]);
                p.path_cells.push_back(tail_cells[idx]);
            }

            pockets.push_back(p);
            for (int c : far_cells) consumed[c] = 1;
        }
        return pockets;
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
    Plan build_binary_counter_plan(int &result_t, bool require_tree) {
        Plan plan;
        result_t = -1;
        vector<Pocket> pockets = find_pockets(require_tree);
        if (pockets.empty()) return plan;

        sort(pockets.begin(), pockets.end(), [](const Pocket &a, const Pocket &b) {
            return a.path_edges.size() < b.path_edges.size();
        });

        // Greedily match the smallest pocket long enough for slot r=1,2,3,...
        // This is the standard interval-scheduling greedy for maximizing
        // the number of consecutive slots covered, which maximizes L (and
        // therefore the 2^L blowup) -- the dominant factor in the final T.
        vector<int> assigned;
        size_t ptr = 0;
        int running_doors = 0;
        int r = 1;
        while (r <= k - 1) {
            while (ptr < pockets.size() && static_cast<int>(pockets[ptr].path_edges.size()) < r) ptr++;
            if (ptr >= pockets.size()) break;
            if (running_doors + r + 1 > m) break; // keep 1 door reserved for the final gate
            assigned.push_back(static_cast<int>(ptr));
            running_doors += r;
            ptr++;
            r++;
        }
        int L = static_cast<int>(assigned.size());
        if (L == 0) return plan;

        vector<int> tin(cells.size(), -1), low(cells.size(), 0), bridges;
        int dfs_timer = 0;
        bridge_dfs(start_id, -1, tin, low, dfs_timer, bridges);
        int final_edge = -1;
        for (int edge_idx : bridges) {
            vector<int> side = start_side_without_edge(edge_idx);
            if (side[goal_id]) continue; // need an essential bridge: goal unreachable without it
            final_edge = edge_idx;
            break;
        }
        if (final_edge < 0) return plan; // no true bottleneck to gate; this construction cannot apply

        plan.switch_cell_type.push_back({start_id, 0});
        for (int i = 1; i <= L; i++) {
            const Pocket &p = pockets[assigned[i - 1]];
            int total_len = static_cast<int>(p.path_edges.size());
            for (int j = 0; j < i; j++) {
                int door_type = (j < i - 1) ? (2 * j) : (2 * (i - 1) + 1);
                plan.door_edge_type.push_back({p.path_edges[j], door_type});
            }
            plan.switch_cell_type.push_back({p.path_cells[total_len - 1], i});
        }
        plan.door_edge_type.push_back({final_edge, 2 * L + 1});

        vector<int> door_type(edges.size(), -1);
        vector<int> switch_type(cells.size(), -1);
        for (const auto &entry : plan.door_edge_type) door_type[entry.first] = entry.second;
        for (const auto &entry : plan.switch_cell_type) switch_type[entry.first] = entry.second;
        result_t = evaluate_arrays(door_type, switch_type);
        return plan;
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
                           t_composite, 1.88);
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
        // in two variants -- tree-only pockets (immune to a cyclic pocket
        // collapsing its own chain) and the fuller cyclic-tolerant pocket
        // pool (more slots available, so higher L, on pocket-scarce mazes
        // where the tree-only pool caps L too low) -- since neither variant
        // dominates the other across mazes. Both are exact-BFS verified and
        // only the strictly-better-than-portfolio result (if any) is used,
        // so output quality never regresses; unreachable (-1) or
        // absent-structure (empty plan, best_t stays -1) results are
        // naturally rejected by the comparison.
        int binary_t_tree = -1;
        AHC067Solver::Plan binary_plan_tree = solver.build_binary_counter_plan(binary_t_tree, true);
        int binary_t_any = -1;
        AHC067Solver::Plan binary_plan_any = solver.build_binary_counter_plan(binary_t_any, false);

        int binary_t = binary_t_tree;
        AHC067Solver::Plan *binary_plan = &binary_plan_tree;
        if (binary_t_any > binary_t) {
            binary_t = binary_t_any;
            binary_plan = &binary_plan_any;
        }

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
