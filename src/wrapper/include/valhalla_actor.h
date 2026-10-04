#ifndef VALHALLAACTOR_H
#define VALHALLAACTOR_H

#include <cstdint>
#include <functional>
#include <string>
#include <valhalla/tyr/actor.h>
#include <valhalla/baldr/tilegetter.h>

class ValhallaMobileHttpClient {
public:
    virtual ~ValhallaMobileHttpClient() = default;
    
    /**
     * Makes a synchronous GET request to fetch tile data
     * @param url the URL to fetch
     * @param range_offset optional offset for range requests
     * @param range_size optional size for range requests
     * @return GET_response_t with the response data and status
     */
    virtual valhalla::baldr::tile_getter_t::GET_response_t 
    get(const std::string& url, uint64_t range_offset = 0, uint64_t range_size = 0) = 0;
    
    /**
     * Makes a synchronous HEAD request to fetch response headers
     * @param url the URL to query
     * @param header_mask mask for which headers to retrieve
     * @return HEAD_response_t with the response headers and status
     */
    virtual valhalla::baldr::tile_getter_t::HEAD_response_t 
    head(const std::string& url, valhalla::baldr::tile_getter_t::header_mask_t header_mask) = 0;
};

class ValhallaActor {
private:
    std::unique_ptr<valhalla::tyr::actor_t> actor;
    std::unique_ptr<valhalla::baldr::GraphReader> graph_reader;
    // Token of the cancellable trace_attributes call in flight (0 = none) and the interrupt the
    // engine polls (meili: once per alternates search round, core patch 0002; baldr: on tile
    // loads). A member, not a local: the core keeps the pointer in its workers until the next call.
    int64_t trace_token = 0;
    std::function<void()> trace_interrupt;
public:
    ValhallaActor(const std::string& config_path, ValhallaMobileHttpClient* http_client = nullptr);
    
    std::string route(const std::string& request);
    std::string traceRoute(const std::string& request);
    std::string traceAttributes(const std::string& request);
    // Same as traceAttributes, but cancelTrace(token) from ANY thread ends it at the engine's next
    // interrupt poll (at most one search round of further work) with TraceCancelled. token > 0.
    std::string traceAttributes(const std::string& request, int64_t token);
    // Cancels the cancellable call carrying `token`, now or when it starts; never touches an actor
    // (safe while another thread is inside a call, or after the actor is gone). A token is the
    // caller's own unique id: a cancel for one call can never stop another.
    static void cancelTrace(int64_t token);
};

// Thrown out of a cancelled traceAttributes(request, token); never escapes the C/JNI surfaces.
struct TraceCancelled {};

#endif // VALHALLAACTOR_H
