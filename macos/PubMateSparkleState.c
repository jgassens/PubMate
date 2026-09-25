#include "PubMateSparkleState.h"

#include <stddef.h>

void pm_sparkle_busy_state_init(PMSparkleBusyState *state)
{
    if (state == NULL) {
        return;
    }
    state->busy = 0;
    state->pending_context = NULL;
    state->pending_invoke = NULL;
    state->pending_dispose = NULL;
    state->log = NULL;
}

void pm_sparkle_busy_state_set_log(PMSparkleBusyState *state, PMSparkleStateLog log)
{
    if (state != NULL) {
        state->log = log;
    }
}

void pm_sparkle_busy_state_set(PMSparkleBusyState *state, int busy)
{
    if (state == NULL) {
        return;
    }

    state->busy = busy != 0;
    if (state->busy || state->pending_context == NULL) {
        return;
    }

    void *context = state->pending_context;
    PMSparkleStateAction invoke = state->pending_invoke;
    state->pending_context = NULL;
    state->pending_invoke = NULL;
    state->pending_dispose = NULL;
    /*
     * Sparkle dispatches its quit event asynchronously after this callback.
     * A new conversion can therefore begin in between; the application's Quit
     * handler asks the user in that benign race, and Sparkle retries otherwise.
     */
    if (invoke != NULL) {
        invoke(context);
    }
}

int pm_sparkle_busy_state_postpone(
    PMSparkleBusyState *state,
    void *context,
    PMSparkleStateAction invoke,
    PMSparkleStateAction dispose
)
{
    if (state == NULL || !state->busy) {
        if (dispose != NULL) {
            dispose(context);
        }
        return 0;
    }

    if (state->pending_context != NULL) {
        if (state->log != NULL) {
            state->log("Sparkle superseded a pending install handler");
        }
        if (state->pending_dispose != NULL) {
            state->pending_dispose(state->pending_context);
        }
    }
    state->pending_context = context;
    state->pending_invoke = invoke;
    state->pending_dispose = dispose;
    return 1;
}
