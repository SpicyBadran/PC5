namespace Pc5.Core.Lifecycle;

/// <summary>
/// Every state the PC5 host application can be in, and the only states it can be in.
///
/// The two states that exist to prevent a lie are <see cref="Unsupported"/> and
/// <see cref="Blocked"/>: neither may be reported as a failure of the user's machine
/// and neither may be smoothed into <see cref="Stopped"/>. A successful Windows launch
/// puts the application in <see cref="Ready"/> and nothing more; getting a guest
/// <see cref="Running"/> is a separate claim that needs separate evidence.
/// </summary>
public enum ExecutionState
{
    /// <summary>No initialization has run. The initial value of a fresh object.</summary>
    NotInitialized,

    /// <summary>Configuration and logging are being brought up.</summary>
    Initializing,

    /// <summary>The host is ready to attempt an execution lifecycle.</summary>
    Ready,

    /// <summary>A backend has been asked to bring a guest up.</summary>
    Starting,

    /// <summary>A guest is executing under the selected backend.</summary>
    Running,

    /// <summary>A running guest has been suspended by request.</summary>
    Paused,

    /// <summary>A guest is being torn down on request.</summary>
    Stopping,

    /// <summary>The lifecycle ended because it was asked to end.</summary>
    Stopped,

    /// <summary>The lifecycle ended because something did not work.</summary>
    Failed,

    /// <summary>The host lacks a capability the selected architecture requires.</summary>
    Unsupported,

    /// <summary>Everything available is in place except an input the project does not hold.</summary>
    Blocked,
}

/// <summary>One recorded state change, with the reason that produced it.</summary>
/// <param name="From">State before the change.</param>
/// <param name="To">State after the change.</param>
/// <param name="Reason">Why the change happened, in the words of the caller.</param>
/// <param name="Utc">When it happened.</param>
public readonly record struct StateChange(
    ExecutionState From,
    ExecutionState To,
    string Reason,
    DateTimeOffset Utc);
