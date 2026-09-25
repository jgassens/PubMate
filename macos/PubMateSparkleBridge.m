#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>

#include <stdlib.h>

#include "PubMateSparkleState.h"


@interface PubMateSparkleDelegate : NSObject <SPUUpdaterDelegate>
@end

static SPUStandardUpdaterController *PubMateSparkleController = nil;
static PubMateSparkleDelegate *PubMateSparkleDelegateObject = nil;
static BOOL PubMateStartAttempted = NO;
static int PubMateStartResult = 1;
static PMSparkleBusyState PubMateBusyState = {0};

static void PubMateDisposeInstallHandler(void *context)
{
    (void)CFBridgingRelease(context);
}

static void PubMateInvokeInstallHandler(void *context)
{
    NSCAssert(
        [NSThread isMainThread],
        @"Sparkle install handler must run on the main thread"
    );
    void (^installHandler)(void) = CFBridgingRelease(context);
    installHandler();
}

static void PubMateLogStateMessage(const char *message)
{
    NSLog(@"PubMate Sparkle state: %s", message);
}

@implementation PubMateSparkleDelegate

- (BOOL)updater:(SPUUpdater *)updater
    shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item
        untilInvokingBlock:(void (^)(void))installHandler
{
    (void)updater;
    (void)item;
    void *context = (__bridge_retained void *)[installHandler copy];
    return pm_sparkle_busy_state_postpone(
        &PubMateBusyState,
        context,
        PubMateInvokeInstallHandler,
        PubMateDisposeInstallHandler
    ) != 0;
}

#if defined(PUBMATE_DEBUG_FEED) && PUBMATE_DEBUG_FEED == 1
- (nullable NSString *)feedURLStringForUpdater:(SPUUpdater *)updater
{
    (void)updater;
    const char *feedURL = getenv("PUBMATE_SPARKLE_FEED_URL");
    return feedURL != NULL && feedURL[0] != '\0'
        ? [NSString stringWithUTF8String:feedURL]
        : nil;
}
#endif

@end

__attribute__((visibility("default")))
int pm_sparkle_start(void)
{
    @autoreleasepool {
        @try {
            if (PubMateStartAttempted) {
                return PubMateStartResult;
            }
            PubMateStartAttempted = YES;
            pm_sparkle_busy_state_init(&PubMateBusyState);
            pm_sparkle_busy_state_set_log(
                &PubMateBusyState,
                PubMateLogStateMessage
            );
            PubMateSparkleDelegateObject = [[PubMateSparkleDelegate alloc] init];
            PubMateSparkleController = [[SPUStandardUpdaterController alloc]
                initWithStartingUpdater:YES
                updaterDelegate:PubMateSparkleDelegateObject
                userDriverDelegate:nil
            ];
            PubMateStartResult = PubMateSparkleController == nil ? 1 : 0;
            return PubMateStartResult;
        } @catch (NSException *exception) {
            NSLog(@"PubMate could not start Sparkle: %@", exception);
            PubMateStartResult = 2;
            return PubMateStartResult;
        }
    }
}

__attribute__((visibility("default")))
void pm_sparkle_check_for_updates(void)
{
    @autoreleasepool {
        @try {
            if (PubMateStartResult == 0 && PubMateSparkleController != nil) {
                [PubMateSparkleController checkForUpdates:nil];
            }
        } @catch (NSException *exception) {
            NSLog(@"PubMate could not check for updates: %@", exception);
        }
    }
}

__attribute__((visibility("default")))
int pm_sparkle_can_check(void)
{
    @autoreleasepool {
        @try {
            if (PubMateStartResult != 0 || PubMateSparkleController == nil) {
                return 0;
            }
            return PubMateSparkleController.updater.canCheckForUpdates ? 1 : 0;
        } @catch (NSException *exception) {
            NSLog(@"PubMate could not query Sparkle: %@", exception);
            return 0;
        }
    }
}

__attribute__((visibility("default")))
void pm_sparkle_set_busy(int busy)
{
    /* ctypes callers should invoke this from Tk's main thread. */
    @autoreleasepool {
        @try {
            if (![NSThread isMainThread]) {
                dispatch_sync(dispatch_get_main_queue(), ^{
                    pm_sparkle_set_busy(busy);
                });
                return;
            }
            pm_sparkle_busy_state_set(&PubMateBusyState, busy);
        } @catch (NSException *exception) {
            NSLog(@"PubMate could not update Sparkle's busy state: %@", exception);
        }
    }
}
