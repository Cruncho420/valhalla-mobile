
#include <valhalla/worker.h>
#include "main.h"
#include "valhalla_actor.h"
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

namespace {
// Engine messages can contain quotes, backslashes, and control characters.
std::string trace_error_json(int code, const char* message) {
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
    writer.StartObject();
    writer.Key("code");
    writer.Int(code);
    writer.Key("message");
    writer.String(message);
    writer.EndObject();
    return std::string(buffer.GetString(), buffer.GetSize());
}

std::string trace_route_result(const char* request, void* actor) {
    try {
        if (!actor || !request) {
            return trace_error_json(-1, "Trace route requires a live actor and request");
        }
        return static_cast<ValhallaActor*>(actor)->traceRoute(request);
    } catch (const valhalla::valhalla_exception_t& error) {
        return trace_error_json(error.code, error.message.c_str());
    } catch (const std::exception& error) {
        return trace_error_json(-1, error.what());
    } catch (...) {
        return trace_error_json(-1, "unknown exception");
    }
}
} // namespace

#ifdef __ANDROID__
// The Android JNI interface uses a different function signature.
#include <jni.h>

extern "C"
JNIEXPORT jstring

JNICALL
Java_com_valhalla_valhalla_ValhallaKotlin_route(JNIEnv *env,
                                                jobject thiz,
                                                jstring jRequest,
                                                jstring jConfigPath) {
    
    const char *request = env->GetStringUTFChars(jRequest, 0);
    const char *config_path = env->GetStringUTFChars(jConfigPath, 0);

    std::string result;
    try {
        // TODO: Android currently creates a new actor every time. Optimize to be like iOS later.
        ValhallaActor valhallaActor(config_path);
        result = valhallaActor.route(request);
    } catch (const valhalla::valhalla_exception_t &err) {
        printf("[ValhallaActor] route valhalla_exception: %s\n", err.what());
        std::string code = std::to_string(err.code);
        std::string message = err.message.c_str();

        result = "{\"code\":" + code + ",\"message\":\"" + message + "\"}";
    } catch (const std::exception &err) {
        printf("[ValhallaActor] route std::exception: %s\n", err.what());
        result = "{\"code\":-1,\"message\":\"" + std::string(err.what()) + "\"}";
    } catch (...) {
        printf("[ValhallaActor] route unknown exception");
        result = "{\"code\":-1,\"message\":\"unknown exception\"}";
    }

    env->ReleaseStringUTFChars(jRequest, request);
    env->ReleaseStringUTFChars(jConfigPath, config_path);

    return env->NewStringUTF(result.c_str());
}

// --- ValhallaRaw persistent-actor JNI surface (Rods r3, additive) ---------------------------
// Android mirror of the Apple create_valhalla_actor / route / delete_valhalla_actor trio above:
// lets com.valhalla.valhalla.ValhallaRaw hold ONE native actor across route calls and destroy it
// deterministically via close(), instead of rebuilding the actor on every request (stock entry
// point above) or leaking it to GC timing. The stock ValhallaKotlin entry point is unchanged.

extern "C"
JNIEXPORT jlong

JNICALL
Java_com_valhalla_valhalla_ValhallaRaw_nativeCreateActor(JNIEnv *env,
                                                         jobject thiz,
                                                         jstring jConfigPath) {
    const char *config_path = env->GetStringUTFChars(jConfigPath, 0);

    ValhallaActor *actor = nullptr;
    try {
        actor = new ValhallaActor(config_path);
    } catch (const std::exception &err) {
        // 0 signals failure to Kotlin; it falls back to the stock per-call path so the caller
        // still gets a Valhalla error-JSON response instead of a Java exception.
        printf("[ValhallaRaw] nativeCreateActor exception: %s\n", err.what());
        actor = nullptr;
    } catch (...) {
        printf("[ValhallaRaw] nativeCreateActor unknown exception\n");
        actor = nullptr;
    }

    env->ReleaseStringUTFChars(jConfigPath, config_path);

    return reinterpret_cast<jlong>(actor);
}

extern "C"
JNIEXPORT jstring

JNICALL
Java_com_valhalla_valhalla_ValhallaRaw_nativeRoute(JNIEnv *env,
                                                   jobject thiz,
                                                   jlong jActorHandle,
                                                   jstring jRequest) {
    const char *request = env->GetStringUTFChars(jRequest, 0);

    std::string result;
    try {
        result = reinterpret_cast<ValhallaActor *>(jActorHandle)->route(request);
    } catch (const valhalla::valhalla_exception_t &err) {
        printf("[ValhallaRaw] route valhalla_exception: %s\n", err.what());
        std::string code = std::to_string(err.code);
        std::string message = err.message.c_str();

        result = "{\"code\":" + code + ",\"message\":\"" + message + "\"}";
    } catch (const std::exception &err) {
        printf("[ValhallaRaw] route std::exception: %s\n", err.what());
        result = "{\"code\":-1,\"message\":\"" + std::string(err.what()) + "\"}";
    } catch (...) {
        printf("[ValhallaRaw] route unknown exception");
        result = "{\"code\":-1,\"message\":\"unknown exception\"}";
    }

    env->ReleaseStringUTFChars(jRequest, request);

    return env->NewStringUTF(result.c_str());
}

extern "C"
JNIEXPORT void

JNICALL
Java_com_valhalla_valhalla_ValhallaRaw_nativeDestroyActor(JNIEnv *env,
                                                          jobject thiz,
                                                          jlong jActorHandle) {
    delete reinterpret_cast<ValhallaActor *>(jActorHandle);
}
// No temporary actor or route fallback: trace_route is a distinct engine action.
extern "C" JNIEXPORT jstring JNICALL
Java_com_valhalla_valhalla_ValhallaRaw_nativeTraceRoute(JNIEnv* env,
                                                     jobject,
                                                     jlong handle,
                                                     jstring input) {
    if (!input) {
        return env->NewStringUTF("{\"code\":-1,\"message\":\"Trace request is null\"}");
    }
    const char* request = env->GetStringUTFChars(input, nullptr);
    if (!request) return nullptr; // Preserve the JVM's pending allocation exception.
    jstring response = nullptr;
    try {
        const auto result = trace_route_result(request, reinterpret_cast<void*>(handle));
        response = env->NewStringUTF(result.c_str());
    } catch (...) {
        // Includes an allocation failure while constructing error JSON.
        response = env->NewStringUTF("{\"code\":-1,\"message\":\"Trace response unavailable\"}");
    }
    env->ReleaseStringUTFChars(input, request);
    return response;
}
// --- end ValhallaRaw JNI surface ------------------------------------------------------------

#elif __APPLE__
std::string trace_route(const char* request, void* actor) {
    return trace_route_result(request, actor);
}

void* create_valhalla_actor(const char *config_path, ValhallaMobileHttpClient* http_client) {
    return new ValhallaActor(config_path, http_client);
}

void delete_valhalla_actor(void* actor) {
    delete ((ValhallaActor*) actor);
}

std::string route(const char *request, void* actor) {
    std::string result;
    try {
        result = ((ValhallaActor*) actor)->route(request);
    } catch (const valhalla::valhalla_exception_t &err) {
        printf("[ValhallaActor] route valhalla_exception: %s\n", err.what());
        std::string code = std::to_string(err.code);
        std::string message = err.message.c_str();

        result = "{\"code\":" + code + ",\"message\":\"" + message + "\"}";
    } catch (const std::exception &err) {
        printf("[ValhallaActor] route std::exception: %s\n", err.what());
        result = "{\"code\":-1,\"message\":\"" + std::string(err.what()) + "\"}";
    } catch (...) {
        printf("[ValhallaActor] route unknown exception");
        result = "{\"code\":-1,\"message\":\"unknown exception\"}";
    }

    return result;
}
#endif
