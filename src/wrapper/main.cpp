
#include <valhalla/worker.h>
#include "main.h"
#include "valhalla_actor.h"

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
// --- end ValhallaRaw JNI surface ------------------------------------------------------------

#elif __APPLE__
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
