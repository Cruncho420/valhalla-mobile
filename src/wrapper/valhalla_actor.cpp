#include <array>
#include <atomic>
#include <boost/property_tree/ptree.hpp>
#include <valhalla/tyr/actor.h>
#include <valhalla/baldr/rapidjson_utils.h>
#include <valhalla/loki/worker.h>
#include "valhalla_actor.h"

class TileGetterWrapper : public valhalla::baldr::tile_getter_t {
public:
  /**
   * @param pool_size  the number of curler instances in the pool
   * @param user_agent  user agent to use by curlers for HTTP requests
   * @param gzipped  whether to request for gzip compressed data
   * @param user_pw  the "user:pwd" for HTTP basic auth
   */
  TileGetterWrapper(ValhallaMobileHttpClient* http_client, bool is_gzipped): http_client(http_client), is_gzipped(is_gzipped) {
  }

  GET_response_t get(const std::string& url,
                     const uint64_t range_offset = 0,
                     const uint64_t range_size = 0) override {
    GET_response_t result;
    if (http_client) { 
        result = http_client->get(url, range_offset, range_size);
    } else {
        result.status_ = tile_getter_t::status_code_t::FAILURE;
    }
    return result;
  }

  HEAD_response_t head(const std::string& url, header_mask_t header_mask) override {
    HEAD_response_t result;
    if (http_client) { 
        result = http_client->head(url, header_mask);
    } else {
        result.status_ = tile_getter_t::status_code_t::FAILURE;
    }
    return result;
  }

  bool gzipped() const override {
    return is_gzipped;
  }

  ~TileGetterWrapper() {
    delete http_client;
  };

private:
  bool is_gzipped;
  ValhallaMobileHttpClient* http_client;
};


ValhallaActor::ValhallaActor(const std::string& config_path, ValhallaMobileHttpClient* http_client) {
std::string config_file(config_path);
    
    // Set up the config object
    boost::property_tree::ptree config;
    rapidjson::read_json(config_file, config);

    auto mjolnir_config = config.get_child("mjolnir");
    graph_reader = std::make_unique<valhalla::baldr::GraphReader>(
      mjolnir_config, 
      std::make_unique<TileGetterWrapper>(http_client, mjolnir_config.get<bool>("tile_url_gz", false))
    );
    // Setup the actor
    actor = std::make_unique<valhalla::tyr::actor_t>(config, *graph_reader, true);
}

std::string ValhallaActor::route(const std::string& request) {
    // Convert the request to a std::string
    std::string req = std::string(request);
    
    // Produce the route result
    std::string result = actor->route(req);
    
    return result;
}

std::string ValhallaActor::traceRoute(const std::string& request) {
    // Trace requests must enter map matching; route() ignores a JSON action override.
    return actor->trace_route(request);
}

std::string ValhallaActor::traceAttributes(const std::string& request) {
    return actor->trace_attributes(request);
}

namespace {
// The most recently cancelled tokens, process-wide (a small ring, so one cancel cannot erase another that its call
// has not polled yet). Callers hand out unique tokens, so "my token is in the ring" means "I was cancelled".
// ponytail: 8 slots; more than 8 cancels landing before one call's next poll would need a per-call flag instead.
constexpr size_t kCancelSlots = 8;
std::array<std::atomic<int64_t>, kCancelSlots> cancelled_tokens{};
std::atomic<size_t> next_cancel_slot{0};

bool is_cancelled(int64_t token) {
    if (token <= 0) return false;
    for (const auto& slot : cancelled_tokens) {
        if (slot.load(std::memory_order_relaxed) == token) return true;
    }
    return false;
}
} // namespace

void ValhallaActor::cancelTrace(int64_t token) {
    if (token <= 0) return;
    cancelled_tokens[next_cancel_slot.fetch_add(1, std::memory_order_relaxed) % kCancelSlots]
        .store(token, std::memory_order_relaxed);
}

std::string ValhallaActor::traceAttributes(const std::string& request, int64_t token) {
    trace_token = token;
    if (!trace_interrupt) {
        trace_interrupt = [this] {
            if (is_cancelled(trace_token)) throw TraceCancelled{};
        };
    }
    try {
        auto result = actor->trace_attributes(request, &trace_interrupt);
        trace_token = 0;
        return result;
    } catch (...) {
        // Recognised by its token, not by the exception that surfaced: TraceCancelled is not a
        // std::exception, but a core catch (...) on the way (e.g. route_match before its map_match
        // fallback) may swallow it and something else surface instead.
        const bool cancelled = is_cancelled(trace_token);
        trace_token = 0;
        if (cancelled) throw TraceCancelled{};
        throw;
    }
}
