namespace Pc5.Core.Lifecycle;

/// <summary>
/// The application lifecycle as an explicit, total transition table.
///
/// The rule this class exists to enforce: a state may only be entered by an edge the
/// table declares. That is what stops "the VM call returned, so we are Running" and
/// "the window closed, so it must have been a clean Stopped" from becoming the same
/// colour. Invalid transitions are refused with an exception naming both states.
/// </summary>
public sealed class ExecutionStateMachine
{
    private static readonly IReadOnlyDictionary<ExecutionState, ExecutionState[]> Allowed =
        new Dictionary<ExecutionState, ExecutionState[]>
        {
            [ExecutionState.NotInitialized] = new[]
            {
                ExecutionState.Initializing, ExecutionState.Unsupported, ExecutionState.Blocked,
            },
            [ExecutionState.Initializing] = new[]
            {
                ExecutionState.Ready, ExecutionState.Failed, ExecutionState.Unsupported,
                ExecutionState.Blocked,
            },
            [ExecutionState.Ready] = new[]
            {
                ExecutionState.Starting, ExecutionState.Blocked, ExecutionState.Unsupported,
                ExecutionState.Stopped,
            },
            [ExecutionState.Starting] = new[]
            {
                ExecutionState.Running, ExecutionState.Failed, ExecutionState.Blocked,
                ExecutionState.Stopping,
            },
            [ExecutionState.Running] = new[]
            {
                ExecutionState.Paused, ExecutionState.Stopping, ExecutionState.Failed,
            },
            [ExecutionState.Paused] = new[]
            {
                ExecutionState.Running, ExecutionState.Stopping, ExecutionState.Failed,
            },
            [ExecutionState.Stopping] = new[] { ExecutionState.Stopped, ExecutionState.Failed },
            // A terminal state may be left only toward a fresh attempt, never sideways.
            [ExecutionState.Stopped] = new[] { ExecutionState.Ready, ExecutionState.Starting },
            [ExecutionState.Failed] = new[] { ExecutionState.Ready, ExecutionState.Blocked },
            [ExecutionState.Unsupported] = new[] { ExecutionState.Blocked },
            [ExecutionState.Blocked] = new[] { ExecutionState.Ready },
        };

    private readonly object Gate = new();
    private readonly List<StateChange> _history = new();

    public ExecutionStateMachine()
    {
        Current = ExecutionState.NotInitialized;
    }

    /// <summary>Raised after a transition has been recorded. Handlers run outside the lock.</summary>
    public event Action<StateChange>? Changed;

    public ExecutionState Current { get; private set; }

    /// <summary>Every transition this object has accepted, oldest first.</summary>
    public IReadOnlyList<StateChange> History
    {
        get { lock (Gate) { return _history.ToArray(); } }
    }

    public static bool CanMove(ExecutionState from, ExecutionState to) =>
        Allowed.TryGetValue(from, out var targets) && Array.IndexOf(targets, to) >= 0;

    /// <summary>Move to <paramref name="to"/>, or throw naming both states.</summary>
    public StateChange MoveTo(ExecutionState to, string reason)
    {
        if (string.IsNullOrWhiteSpace(reason))
        {
            throw new ArgumentException("A state change without a reason is not diagnosable.", nameof(reason));
        }

        StateChange change;
        lock (Gate)
        {
            if (!CanMove(Current, to))
            {
                throw new InvalidOperationException(
                    $"refused lifecycle transition {Current} -> {to} (reason given: {reason}). " +
                    $"Declared targets from {Current}: {Declared(from: Current)}.");
            }

            change = new StateChange(Current, to, reason.Trim(), DateTimeOffset.UtcNow);
            _history.Add(change);
            Current = to;
        }

        Changed?.Invoke(change);
        return change;
    }

    /// <summary>Record a state with no edge: only for restoring persisted status, never for live flow.</summary>
    internal void Seed(ExecutionState state, string reason)
    {
        lock (Gate)
        {
            Current = state;
            _history.Add(new StateChange(state, state, reason.Trim(), DateTimeOffset.UtcNow));
        }
    }

    private static string Declared(ExecutionState from) =>
        Allowed.TryGetValue(from, out var targets)
            ? string.Join(", ", targets)
            : "no declared targets";
}
