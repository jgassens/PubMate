#ifndef PUBMATE_SPARKLE_STATE_H
#define PUBMATE_SPARKLE_STATE_H

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*PMSparkleStateAction)(void *context);
typedef void (*PMSparkleStateLog)(const char *message);

typedef struct {
    int busy;
    void *pending_context;
    PMSparkleStateAction pending_invoke;
    PMSparkleStateAction pending_dispose;
    PMSparkleStateLog log;
} PMSparkleBusyState;

void pm_sparkle_busy_state_init(PMSparkleBusyState *state);
void pm_sparkle_busy_state_set_log(
    PMSparkleBusyState *state,
    PMSparkleStateLog log
);
void pm_sparkle_busy_state_set(PMSparkleBusyState *state, int busy);
int pm_sparkle_busy_state_postpone(
    PMSparkleBusyState *state,
    void *context,
    PMSparkleStateAction invoke,
    PMSparkleStateAction dispose
);

#ifdef __cplusplus
}
#endif

#endif
