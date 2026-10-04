// Desktop proof harness for the r6 wrapper (FEAT-090 phone bound lane). Runs the PHONE wrapper's own
// ValhallaActor (src/wrapper, r6 branch) on Linux against the r5 core, on the London graph.
//   corpus <config> <requests.jsonl>        one request per line; each in a fresh forked child:
//                                            {"i":n,"ms":t,"peakMB":VmHWM,"raw":<answer>}
//   polls  <config> <request.json>          uncancelled run with a timestamping interrupt (tyr::actor_t
//                                            directly): every poll time -> the longest gap between polls
//   cancel <config> <request.json> <ms>     cancellable call (token 42) cancelled <ms> after start from
//                                            another thread: cancel->return latency, RSS around it
#include "valhalla_actor.h"
#include <valhalla/tyr/actor.h>
#include <valhalla/exceptions.h>
#include <valhalla/baldr/rapidjson_utils.h>
#include <boost/property_tree/ptree.hpp>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

using clk = std::chrono::steady_clock;
static double ms_since(clk::time_point t) { return std::chrono::duration<double, std::milli>(clk::now() - t).count(); }
static long status_kb(const char* key) {
  std::ifstream f("/proc/self/status"); std::string line;
  while (std::getline(f, line)) if (line.rfind(key, 0) == 0) return std::stol(line.substr(strlen(key)));
  return -1;
}
static std::string slurp(const char* p) { std::ifstream f(p); std::stringstream s; s << f.rdbuf(); return s.str(); }
static std::string code_of(const std::string& raw) {
  auto at = raw.find("\"code\":"); if (at == std::string::npos) at = raw.find("\"error_code\":");
  if (at == std::string::npos) return "answer";
  return raw.substr(at, raw.find_first_of(",}", at) - at);
}

static int corpus(const char* config, const char* path) {
  std::ifstream in(path); std::string req; int i = 0;
  while (std::getline(in, req)) {
    int fd[2]; pipe(fd);
    pid_t pid = fork();
    if (pid == 0) {
      close(fd[0]);
      ValhallaActor actor(config);
      auto t = clk::now();
      std::string raw;
      try { raw = actor.traceAttributes(req); } catch (const std::exception& e) { raw = std::string("{\"code\":-1,\"message\":\"") + e.what() + "\"}"; }
      std::ostringstream o; o << "{\"i\":" << i << ",\"ms\":" << (long)ms_since(t) << ",\"peakMB\":" << status_kb("VmHWM:") / 1024 << ",\"raw\":" ;
      std::string esc; for (char c : raw) { if (c == '"' || c == '\\') esc += '\\'; esc += c; }
      o << '"' << esc << "\"}\n";
      auto s = o.str(); write(fd[1], s.data(), s.size()); _exit(0);
    }
    close(fd[1]); std::string out; char buf[65536]; ssize_t n;
    while ((n = read(fd[0], buf, sizeof buf)) > 0) out.append(buf, n);
    close(fd[0]); int st; waitpid(pid, &st, 0);
    if (out.empty()) out = "{\"i\":" + std::to_string(i) + ",\"killed\":true}\n";
    std::cout << out << std::flush; ++i;
  }
  return 0;
}

static int polls(const char* config, const char* path) {
  boost::property_tree::ptree pt; rapidjson::read_json(config, pt);
  valhalla::tyr::actor_t actor(pt, true);
  std::vector<double> at; auto t = clk::now();
  const std::function<void()> interrupt = [&] { at.push_back(ms_since(t)); };
  std::string raw;
  try { raw = actor.trace_attributes(slurp(path), &interrupt); }
  catch (const valhalla::valhalla_exception_t& e) { raw = "{\"code\":" + std::to_string(e.code) + "}"; }
  catch (const std::exception& e) { raw = std::string("{\"code\":\"") + e.what() + "\"}"; }
  double total = ms_since(t), maxGap = 0, prev = 0; int maxAt = 0;
  for (size_t k = 0; k < at.size(); ++k) { if (at[k] - prev > maxGap) { maxGap = at[k] - prev; maxAt = k; } prev = at[k]; }
  if (total - prev > maxGap) { maxGap = total - prev; maxAt = at.size(); }
  std::cout << "{\"mode\":\"polls\",\"totalMs\":" << (long)total << ",\"polls\":" << at.size()
            << ",\"maxGapMs\":" << maxGap << ",\"maxGapAfterPoll\":" << maxAt << ",\"peakMB\":" << status_kb("VmHWM:") / 1024
            << ",\"result\":\"" << code_of(raw) << "\",\"pollMs\":[";
  for (size_t k = 0; k < at.size(); ++k) std::cout << (k ? "," : "") << (long)at[k];
  std::cout << "]}\n";
  return 0;
}

static int cancel(const char* config, const char* path, int after, bool next) {
  ValhallaActor actor(config);
  const std::string req = slurp(path);
  std::atomic<bool> done{false}; long rssAtCancel = 0, rssMaxAfter = 0; double cancelAt = -1;
  auto t = clk::now();
  std::thread canceller([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(after));
    if (done) return;
    rssAtCancel = status_kb("VmRSS:"); cancelAt = ms_since(t);
    ValhallaActor::cancelTrace(42);
    while (!done) { rssMaxAfter = std::max(rssMaxAfter, status_kb("VmRSS:")); std::this_thread::sleep_for(std::chrono::milliseconds(2)); }
  });
  std::string raw;
  try { raw = actor.traceAttributes(req, 42); } catch (const TraceCancelled&) { raw = "{\"code\":-2}"; }
  catch (const valhalla::valhalla_exception_t& e) { raw = "{\"code\":" + std::to_string(e.code) + "}"; }
  double end = ms_since(t); done = true; canceller.join();
  long rssAfter = status_kb("VmRSS:");
  std::cout << "{\"mode\":\"cancel\",\"cancelAtMs\":" << cancelAt << ",\"returnedAtMs\":" << end
            << ",\"latencyMs\":" << (cancelAt < 0 ? -1 : end - cancelAt) << ",\"result\":\"" << code_of(raw).substr(code_of(raw).find(':') + 1)
            << "\",\"rssAtCancelMB\":" << rssAtCancel / 1024 << ",\"rssMaxAfterCancelMB\":" << rssMaxAfter / 1024
            << ",\"rssAfterReturnMB\":" << rssAfter / 1024 << ",\"peakMB\":" << status_kb("VmHWM:") / 1024
            << "}" << std::endl;
  if (!next) return 0;
  // Same actor afterwards: an uncancelled token runs normally.
  auto t2 = clk::now(); std::string again;
  try { again = actor.traceAttributes(req, 43); }
  catch (const valhalla::valhalla_exception_t& e) { again = "{\"code\":" + std::to_string(e.code) + "}"; }
  std::cout << "{\"nextCallMs\":" << (long)ms_since(t2) << ",\"nextCall\":\"" << code_of(again).substr(code_of(again).find(':') + 1) << "\"}" << std::endl;
  return 0;
}

int main(int argc, char** argv) {
  std::string mode = argc > 1 ? argv[1] : "";
  if (mode == "corpus" && argc == 4) return corpus(argv[2], argv[3]);
  if (mode == "polls" && argc == 4) return polls(argv[2], argv[3]);
  if (mode == "cancel" && (argc == 5 || argc == 6)) return cancel(argv[2], argv[3], atoi(argv[4]), argc == 5);
  std::cerr << "usage: corpus|polls <config> <file> | cancel <config> <request> <ms>\n"; return 2;
}
