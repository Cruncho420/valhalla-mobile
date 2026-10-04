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
// One process-wide slot: the most recently cancelled token. Callers hand out unique tokens, so
// "the slot holds my token" means "I was cancelled" whatever else ran in between.
std::atomic<int64_t> cancelled_trace_token{0};
} // namespace

void ValhallaActor::cancelTrace(int64_t token) {
    cancelled_trace_token.store(token, std::memory_order_relaxed);
}

std::string ValhallaActor::traceAttributes(const std::string& request, int64_t token) {
    trace_token = token;
    if (!trace_interrupt) {
        trace_interrupt = [this] {
            if (trace_token > 0 && cancelled_trace_token.load(std::memory_order_relaxed) == trace_token) {
                throw TraceCancelled{};
            }
        };
    }
    try {
        auto result = actor->trace_attributes(request, &trace_interrupt);
        trace_token = 0;
        return result;
    } catch (...) {
        // The core wraps whatever the interrupt throws (meili's failure becomes a 444 "no match"),
        // so a cancelled call is recognised by its token, not by the exception that surfaced.
        const bool cancelled = trace_token > 0 &&
            cancelled_trace_token.load(std::memory_order_relaxed) == trace_token;
        trace_token = 0;
        if (cancelled) throw TraceCancelled{};
        throw;
    }
}
