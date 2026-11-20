#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <chrono>

namespace py = pybind11;
using namespace std;

// ==========================================
// 極速圖論搜尋引擎 (FastGraph)
// ==========================================
class FastGraph {
private:
    unordered_map<string, vector<pair<string, string>>> forward_adj;
    unordered_map<string, pair<string, vector<string>>> reverse_adj;

public:
    void add_net_driver(const string& net_name, const string& gate_name, const vector<string>& in_nets) {
        reverse_adj[net_name] = {gate_name, in_nets};
        for (const string& in_net : in_nets) {
            forward_adj[in_net].push_back({gate_name, net_name});
        }
    }

    vector<string> find_paths(const string& start_net, const string& end_net, const string& avoid_net, int max_paths, double time_limit_sec) {
        vector<string> all_paths;
        
        // 階段一：反向 BFS 導航
        unordered_set<string> can_reach_end;
        vector<string> q = {end_net};
        can_reach_end.insert(end_net);
        int head = 0;
        
        while(head < q.size()) {
            string curr = q[head++];
            if (!avoid_net.empty() && curr == avoid_net) continue;
            
            if (reverse_adj.find(curr) != reverse_adj.end()) {
                for (const string& prev_net : reverse_adj[curr].second) {
                    if (!avoid_net.empty() && prev_net == avoid_net) continue;
                    if (can_reach_end.find(prev_net) == can_reach_end.end()) {
                        can_reach_end.insert(prev_net);
                        q.push_back(prev_net);
                    }
                }
            }
        }

        if (can_reach_end.find(start_net) == can_reach_end.end()) return all_paths; 

        // 階段二：正向 DFS
        vector<string> path_stack = {start_net};
        unordered_set<string> visited_gates;
        auto start_time = chrono::steady_clock::now();
        long long step_count = 0;
        bool timeout = false;

        auto dfs = [&](auto& self, const string& current_net) -> void {
            if (timeout || all_paths.size() >= max_paths) return;

            step_count++;
            if (step_count % 10000 == 0) {
                auto now = chrono::steady_clock::now();
                chrono::duration<double> elapsed = now - start_time;
                if (elapsed.count() > time_limit_sec) {
                    timeout = true;
                    return;
                }
            }

            if (current_net == end_net) {
                string path_str = "";
                for (size_t i = 0; i < path_stack.size(); ++i) {
                    path_str += path_stack[i];
                    if (i != path_stack.size() - 1) path_str += "->";
                }
                all_paths.push_back(path_str);
                return;
            }

            for (const auto& next_step : forward_adj[current_net]) {
                const string& gate_name = next_step.first;
                const string& next_net = next_step.second;

                if (!avoid_net.empty() && next_net == avoid_net) continue;
                if (visited_gates.count(gate_name)) continue;
                if (can_reach_end.find(next_net) == can_reach_end.end()) continue;

                visited_gates.insert(gate_name);
                path_stack.push_back(gate_name);
                path_stack.push_back(next_net);
                
                self(self, next_net);
                
                path_stack.pop_back();
                path_stack.pop_back();
                visited_gates.erase(gate_name);
            }
        };

        dfs(dfs, start_net);
        return all_paths;
    }
};

PYBIND11_MODULE(fast_eda, m) {
    m.doc() = "Fast EDA C++ Engine with FastGraph (Mockturtle removed)";
    py::class_<FastGraph>(m, "FastGraph")
        .def(py::init<>())
        .def("add_net_driver", &FastGraph::add_net_driver)
        .def("find_paths", &FastGraph::find_paths);
}