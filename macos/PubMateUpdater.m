#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>
#import <mach-o/dyld.h>

#include <limits.h>
#include <stdio.h>

@interface PubMateUpdateDelegate : NSObject <SPUUpdaterDelegate>
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

@end

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

int main(int argc, const char *argv[])
{
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];

        NSBundle *hostBundle = PubMateHostBundle();
        if (hostBundle == nil || !PubMateForcedUpdateMetadataIsValid(hostBundle)) {
            fprintf(stderr, "PubMate updater could not validate the host app or its forced-update settings.\n");
            return 2;
        }
        PubMateClearSavedSkipChoices(hostBundle);

        SPUStandardUserDriver *userDriver = [
            [SPUStandardUserDriver alloc] initWithHostBundle:hostBundle delegate:nil
        ];
        PubMateUpdateDelegate *delegate = [[PubMateUpdateDelegate alloc] init];
        SPUUpdater *updater = [
            [SPUUpdater alloc]
            initWithHostBundle:hostBundle
            applicationBundle:hostBundle
            userDriver:userDriver
            delegate:delegate
        ];

        if (updater == nil) {
            fprintf(stderr, "PubMate updater could not initialize Sparkle.\n");
            return 3;
        }

        if (argc > 1 && strcmp(argv[1], "--self-test") == 0) {
            printf("PubMate native Sparkle forced-update self-test OK\n");
            return 0;
        }

        NSError *startError = nil;
        if (![updater startUpdater:&startError]) {
            fprintf(
                stderr,
                "PubMate updater could not start Sparkle: %s\n",
                startError.localizedDescription.UTF8String
            );
            return 4;
        }

        // These assignments deliberately override stored user preferences.
        updater.automaticallyChecksForUpdates = YES;
        updater.automaticallyDownloadsUpdates = YES;
        if (!updater.automaticallyChecksForUpdates ||
            !updater.automaticallyDownloadsUpdates ||
            !updater.allowsAutomaticUpdates) {
            fprintf(stderr, "PubMate updater could not enforce automatic updates.\n");
            return 5;
        }
        [updater checkForUpdatesInBackground];

        // Keep the native helper alive while Sparkle checks and, when needed,
        // downloads and prepares the update for silent installation on quit.
        NSDate *sessionStartDeadline = [NSDate dateWithTimeIntervalSinceNow:10.0];
        NSDate *hardDeadline = [NSDate dateWithTimeIntervalSinceNow:1800.0];
        BOOL observedSession = NO;

        while ([hardDeadline timeIntervalSinceNow] > 0.0) {
            [[NSRunLoop mainRunLoop]
                runMode:NSDefaultRunLoopMode
                beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.1]
            ];

            if (updater.sessionInProgress) {
                observedSession = YES;
            }
            if (delegate.finished) {
                if (delegate.cycleError != nil) {
                    fprintf(
                        stderr,
                        "PubMate background update cycle ended: %s\n",
                        delegate.cycleError.localizedDescription.UTF8String
                    );
                }
                return 0;
            }
            if (!observedSession && [sessionStartDeadline timeIntervalSinceNow] <= 0.0) {
                return 0;
            }
        }

        fprintf(stderr, "PubMate background update cycle timed out.\n");
        return 6;
    }
}
