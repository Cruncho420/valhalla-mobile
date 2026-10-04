#ifndef WRAPPER_H
#define WRAPPER_H

#include "valhalla_actor.h"

#ifdef __ANDROID__
#include <jni.h>


#ifdef __cplusplus
extern "C" {
#endif

JNIEXPORT jstring JNICALL Java_com_valhalla_valhalla_ValhallaKotlin_route(JNIEnv *env,
                                                jobject thiz,
                                                jstring jRequest,
                                                jstring jConfigPath);

// ValhallaRaw persistent-actor surface (Rods r3): Android mirror of the Apple
// create_valhalla_actor / delete_valhalla_actor pair below. Additive; the stock
// ValhallaKotlin per-call entry point above is unchanged.
JNIEXPORT jlong JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeCreateActor(JNIEnv *env,
                                                jobject thiz,
                                                jstring jConfigPath);

JNIEXPORT jstring JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeRoute(JNIEnv *env,
                                                jobject thiz,
                                                jlong jActorHandle,
                                                jstring jRequest);

JNIEXPORT jstring JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeTraceRoute(JNIEnv *env,
                                                jobject thiz,
                                                jlong jActorHandle,
                                                jstring jRequest);

JNIEXPORT jstring JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeTraceAttributes(JNIEnv *env,
                                                jobject thiz,
                                                jlong jActorHandle,
                                                jstring jRequest);

JNIEXPORT jstring JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeTraceAttributesCancellable(JNIEnv *env,
                                                jobject thiz,
                                                jlong jActorHandle,
                                                jstring jRequest,
                                                jlong jToken);

JNIEXPORT void JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeCancelTrace(JNIEnv *env,
                                                jobject thiz,
                                                jlong jToken);

JNIEXPORT void JNICALL Java_com_valhalla_valhalla_ValhallaRaw_nativeDestroyActor(JNIEnv *env,
                                                jobject thiz,
                                                jlong jActorHandle);

#ifdef __cplusplus
}
#endif

#elif __APPLE__

std::string route(const char *request, void* actor);
std::string trace_route(const char *request, void* actor);
std::string trace_attributes(const char *request, void* actor);
// Cancellable trace_attributes (Rods r6): cancel_trace(token) from any thread ends the call at the
// engine's next interrupt poll; it then answers {"code":-2,"message":"Trace cancelled"}.
std::string trace_attributes_cancellable(const char *request, void* actor, int64_t token);
void cancel_trace(int64_t token);
void* create_valhalla_actor(const char *config_path, ValhallaMobileHttpClient* http_client = nullptr);
void delete_valhalla_actor(void* actor);

#endif

#endif // WRAPPER_H
