#ifndef ValhallaWrapperHeader_h
#define ValhallaWrapperHeader_h

#import <Foundation/Foundation.h>

@class ValhallaWrapper;

@interface ValhallaWrapper : NSObject {
    @private
    void* _actor;
}

- (instancetype)initWithConfigPath:(NSString*)config_path error:(__autoreleasing NSError **)error;

- (NSString*)route:(NSString*)request;

- (NSString*)traceRoute:(NSString*)request;

- (NSString*)traceAttributes:(NSString*)request;

/// Rods r6: -traceAttributes: that +cancelTrace: can stop at the engine's next interrupt poll (at most
/// one alternates-search round of further work); a cancelled call answers {"code":-2,...}. token > 0,
/// unique per call (the caller numbers them).
- (NSString*)traceAttributes:(NSString*)request token:(int64_t)token;

/// Cancels the cancellable call carrying token, running now or later. Class method: touches no
/// actor and takes no lock, so it is safe while another thread is inside a call.
+ (void)cancelTrace:(int64_t)token;

@end

#endif /* ValhallaWrapperHeader_h */
