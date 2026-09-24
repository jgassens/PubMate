#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>
#import <mach-o/dyld.h>

#include <limits.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>

@interface PubMateUpdateDelegate : NSObject <SPUUpdaterDelegate, SPUStandardUserDriverDelegate>
@property(nonatomic, assign) BOOL finished;
@property(nonatomic, strong, nullable) NSError *cycleError;
@end

@implementation PubMateUpdateDelegate

- (void)updater:(SPUUpdater *)updater
    didFinishUpdateCycleForUpdateCheck:(SPUUpdateCheck)updateCheck
                                 error:(nullable NSError *)error
{
    (void)updater;
    (void)updateCheck;
    self.cycleError = error;
    self.finished = YES;
}

- (void)standardUserDriverWillFinishUpdateSession
{
    // The run loop keys off visible Sparkle windows instead.
}

@end

static const NSTimeInterval PubMateBackgroundUpdateTimeout = 30.0 * 60.0;
static const NSTimeInterval PubMateManualUpdateTimeout = 4.0 * 60.0 * 60.0;
static const NSTimeInterval PubMateAbsoluteUpdateTimeout = 12.0 * 60.0 * 60.0;

static NSBundle *PubMateHostBundle(void)
{
    uint32_t size = PATH_MAX;
    char executablePath[PATH_MAX];
    if (_NSGetExecutablePath(executablePath, &size) != 0) {
        return nil;
    }

    NSString *resolvedPath = [
        [NSString stringWithUTF8String:executablePath]
        stringByResolvingSymlinksInPath
    ];
    NSURL *helperURL = [NSURL fileURLWithPath:resolvedPath];
    NSURL *appURL = [[[
        helperURL URLByDeletingLastPathComponent
    ] URLByDeletingLastPathComponent] URLByDeletingLastPathComponent];
    return [NSBundle bundleWithURL:appURL];
}

static BOOL PubMateForcedUpdateMetadataIsValid(NSBundle *hostBundle)
{
    NSString *feedURL = [hostBundle objectForInfoDictionaryKey:@"SUFeedURL"];
    NSNumber *automaticChecks = [hostBundle objectForInfoDictionaryKey:@"SUEnableAutomaticChecks"];
    NSNumber *allowsAutomaticUpdates = [hostBundle objectForInfoDictionaryKey:@"SUAllowsAutomaticUpdates"];
    NSNumber *automaticallyUpdates = [hostBundle objectForInfoDictionaryKey:@"SUAutomaticallyUpdate"];
    NSNumber *promptOnFirstLaunch = [hostBundle objectForInfoDictionaryKey:@"SUPromptUserOnFirstLaunch"];

    return feedURL.length > 0 &&
        automaticChecks.boolValue &&
        allowsAutomaticUpdates.boolValue &&
        automaticallyUpdates.boolValue &&
        !promptOnFirstLaunch.boolValue;
}

static void PubMateClearSavedSkipChoices(NSBundle *hostBundle)
{
    NSString *bundleIdentifier = hostBundle.bundleIdentifier;
    if (bundleIdentifier == nil) {
        return;
    }

    NSUserDefaults *defaults;
    if ([hostBundle isEqual:[NSBundle mainBundle]]) {
        defaults = [NSUserDefaults standardUserDefaults];
    } else {
        defaults = [[NSUserDefaults alloc] initWithSuiteName:bundleIdentifier];
    }
    for (NSString *key in @[
        @"SUSkippedVersion",
        @"SUSkippedMajorVersion",
        @"SUSkippedMajorSubreleaseVersion",
    ]) {
        [defaults removeObjectForKey:key];
    }
}

static void PubMateShowAlert(NSString *message, NSString *detail)
{
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = message;
    alert.informativeText = detail;
    [alert addButtonWithTitle:@"OK"];
    [alert runModal];
}

static int PubMateAcquireUpdateLock(void)
{
    NSArray<NSString *> *applicationSupportDirectories = NSSearchPathForDirectoriesInDomains(
        NSApplicationSupportDirectory,
        NSUserDomainMask,
        YES
    );
    if (applicationSupportDirectories.count == 0) {
        return -1;
    }

    NSString *lockDirectory = [applicationSupportDirectories[0]
        stringByAppendingPathComponent:@"PubMate"];
    NSError *directoryError = nil;
    if (![[NSFileManager defaultManager]
        createDirectoryAtPath:lockDirectory
        withIntermediateDirectories:YES
        attributes:nil
        error:&directoryError
    ]) {
        return -1;
    }

    NSString *lockPath = [lockDirectory stringByAppendingPathComponent:@"updater.lock"];
    int lockFile = open(lockPath.fileSystemRepresentation, O_CREAT | O_RDWR, 0600);
    if (lockFile < 0) {
        return -1;
    }
    if (flock(lockFile, LOCK_EX | LOCK_NB) != 0) {
        int lockError = errno;
        close(lockFile);
        return lockError == EWOULDBLOCK ? -2 : -1;
    }
    return lockFile;
}

static BOOL PubMateHasVisibleSparkleWindow(void)
{
    for (NSWindow *window in NSApp.windows) {
        if (window.isVisible) {
            return YES;
        }
    }
    return NO;
}

int main(int argc, const char *argv[])
{
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];

        BOOL checkNow = argc > 1 && strcmp(argv[1], "--check-now") == 0;
        if (checkNow) {
            // Accessory permits Sparkle's standard UI (and startup-failure
            // alerts) to become key without adding a second PubMate Dock icon.
            [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
            [NSApp activateIgnoringOtherApps:YES];
        }

        NSBundle *hostBundle = PubMateHostBundle();
        if (hostBundle == nil || !PubMateForcedUpdateMetadataIsValid(hostBundle)) {
            fprintf(stderr, "PubMate updater could not validate the host app or its forced-update settings.\n");
            if (checkNow) {
                PubMateShowAlert(
                    @"Cannot Check for Updates",
                    @"PubMate could not find or validate the installed app. Reinstall PubMate and try again."
                );
            }
            return 2;
        }
        PubMateClearSavedSkipChoices(hostBundle);

        PubMateUpdateDelegate *delegate = [[PubMateUpdateDelegate alloc] init];
        SPUStandardUserDriver *userDriver = [
            [SPUStandardUserDriver alloc] initWithHostBundle:hostBundle delegate:delegate
        ];
        SPUUpdater *updater = [
            [SPUUpdater alloc]
            initWithHostBundle:hostBundle
            applicationBundle:hostBundle
            userDriver:userDriver
            delegate:delegate
        ];

        if (updater == nil) {
            fprintf(stderr, "PubMate updater could not initialize Sparkle.\n");
            if (checkNow) {
                PubMateShowAlert(
                    @"Cannot Check for Updates",
                    @"PubMate could not initialize its update service. Quit and reopen PubMate, then try again."
                );
            }
            return 3;
        }

        if (argc > 1 && strcmp(argv[1], "--self-test") == 0) {
            printf("PubMate native Sparkle forced-update self-test OK\n");
            return 0;
        }

        int lockFile = PubMateAcquireUpdateLock();
        if (lockFile < 0) {
            if (checkNow) {
                NSString *detail = lockFile == -2
                    ? @"A PubMate update check is already running. Try again shortly."
                    : @"PubMate could not coordinate the update check. Try again in a moment.";
                PubMateShowAlert(@"Cannot Check for Updates", detail);
            }
            return lockFile == -2 ? 0 : 7;
        }

        NSError *startError = nil;
        if (![updater startUpdater:&startError]) {
            NSString *startErrorDetail = startError.localizedDescription ?:
                @"PubMate could not start its update service. Try again shortly.";
            fprintf(
                stderr,
                "PubMate updater could not start Sparkle: %s\n",
                startErrorDetail.UTF8String
            );
            if (checkNow) {
                PubMateShowAlert(
                    @"Cannot Check for Updates",
                    startErrorDetail
                );
            }
            return 4;
        }

        // These assignments deliberately override stored user preferences.
        updater.automaticallyChecksForUpdates = YES;
        updater.automaticallyDownloadsUpdates = YES;
        if (!updater.automaticallyChecksForUpdates ||
            !updater.automaticallyDownloadsUpdates ||
            !updater.allowsAutomaticUpdates) {
            fprintf(stderr, "PubMate updater could not enforce automatic updates.\n");
            if (checkNow) {
                PubMateShowAlert(
                    @"Cannot Check for Updates",
                    @"PubMate could not configure its update service. Quit and reopen PubMate, then try again."
                );
            }
            return 5;
        }
        if (checkNow) {
            [updater checkForUpdates];
        } else {
            [updater checkForUpdatesInBackground];
        }

        // Keep the native helper alive while Sparkle checks and, when needed,
        // downloads and prepares the update for silent installation on quit.
        NSDate *sessionStartDeadline = [NSDate dateWithTimeIntervalSinceNow:10.0];
        NSTimeInterval timeout = checkNow
            ? PubMateManualUpdateTimeout
            : PubMateBackgroundUpdateTimeout;
        NSDate *timeoutDeadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
        NSDate *absoluteDeadline = [NSDate
            dateWithTimeIntervalSinceNow:PubMateAbsoluteUpdateTimeout
        ];
        BOOL observedSession = NO;

        while (YES) {
            [[NSRunLoop mainRunLoop]
                runMode:NSDefaultRunLoopMode
                beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.1]
            ];

            if (updater.sessionInProgress) {
                observedSession = YES;
            }
            BOOL hasVisibleSparkleWindow = PubMateHasVisibleSparkleWindow();
            if ([absoluteDeadline timeIntervalSinceNow] <= 0.0) {
                break;
            }
            if (delegate.finished) {
                // The user-driver callback may precede or follow the updater-cycle
                // callback. Keep either mode alive until any UI is dismissed.
                if (hasVisibleSparkleWindow) {
                    continue;
                }
                if (delegate.cycleError != nil) {
                    fprintf(
                        stderr,
                        checkNow
                            ? "PubMate manual update cycle ended: %s\n"
                            : "PubMate background update cycle ended: %s\n",
                        delegate.cycleError.localizedDescription.UTF8String
                    );
                }
                return 0;
            }
            if (!checkNow &&
                !observedSession &&
                [sessionStartDeadline timeIntervalSinceNow] <= 0.0) {
                if (hasVisibleSparkleWindow) {
                    continue;
                }
                return 0;
            }
            if ([timeoutDeadline timeIntervalSinceNow] <= 0.0) {
                // A session flag can remain set after a closable Sparkle status
                // window is dismissed. Past the normal timeout, only visible UI
                // keeps either mode alive, and never beyond the absolute cap.
                if (hasVisibleSparkleWindow) {
                    continue;
                }
                break;
            }
        }

        fprintf(
            stderr,
            checkNow
                ? "PubMate manual update cycle timed out.\n"
                : "PubMate background update cycle timed out.\n"
        );
        if (checkNow) {
            PubMateShowAlert(
                @"Cannot Check for Updates",
                @"PubMate couldn't finish checking for updates. Please try again later."
            );
        }
        return 6;
    }
}
