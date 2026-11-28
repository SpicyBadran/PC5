using Pc5.Core.Lifecycle;
using Xunit;

namespace Pc5.Core.Tests;

public class ExecutionStateMachineTests
{
    [Fact]
    public void StartsAtNotInitialized()
    {
        var machine = new ExecutionStateMachine();
        Assert.Equal(ExecutionState.NotInitialized, machine.Current);
        Assert.Empty(machine.History);
    }

    [Theory]
    [InlineData(ExecutionState.NotInitialized, ExecutionState.Initializing, true)]
    [InlineData(ExecutionState.NotInitialized, ExecutionState.Running, false)]
    [InlineData(ExecutionState.NotInitialized, ExecutionState.Stopped, false)]
    [InlineData(ExecutionState.Ready, ExecutionState.Starting, true)]
    [InlineData(ExecutionState.Ready, ExecutionState.Running, false)]
    [InlineData(ExecutionState.Starting, ExecutionState.Running, true)]
    [InlineData(ExecutionState.Running, ExecutionState.Stopped, false)]
    [InlineData(ExecutionState.Running, ExecutionState.Stopping, true)]
    [InlineData(ExecutionState.Failed, ExecutionState.Stopped, false)]
    [InlineData(ExecutionState.Failed, ExecutionState.Ready, true)]
    [InlineData(ExecutionState.Unsupported, ExecutionState.Ready, false)]
    [InlineData(ExecutionState.Blocked, ExecutionState.Ready, true)]
    public void TransitionTableIsWhatTheApplicationMayBelieve(ExecutionState from, ExecutionState to, bool allowed)
    {
        Assert.Equal(allowed, ExecutionStateMachine.CanMove(from, to));
    }

    [Fact]
    public void AnEndedLifecycleIsNeverRewrittenAsACleanStop()
    {
        var machine = new ExecutionStateMachine();
        machine.MoveTo(ExecutionState.Initializing, "startup");
        machine.MoveTo(ExecutionState.Ready, "ready");
        machine.MoveTo(ExecutionState.Starting, "attempt");
        machine.MoveTo(ExecutionState.Failed, "the backend refused");

        var refused = Assert.Throws<InvalidOperationException>(() =>
            machine.MoveTo(ExecutionState.Stopped, "pretend it ended cleanly"));

        Assert.Contains("Failed", refused.Message, StringComparison.Ordinal);
        Assert.Contains("Stopped", refused.Message, StringComparison.Ordinal);
        Assert.Equal(ExecutionState.Failed, machine.Current);
    }

    [Fact]
    public void EveryTransitionCarriesAReasonAndIsRecordedInOrder()
    {
        var machine = new ExecutionStateMachine();
        machine.MoveTo(ExecutionState.Initializing, "startup begins");
        machine.MoveTo(ExecutionState.Ready, "no blocker found");

        var history = machine.History;
        Assert.Equal(2, history.Count);
        Assert.Equal(ExecutionState.NotInitialized, history[0].From);
        Assert.Equal("startup begins", history[1].Reason);
        Assert.True(history[1].Utc <= DateTimeOffset.UtcNow);
        Assert.Equal(ExecutionState.Ready, history[1].From);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    public void AStateChangeWithoutAReasonIsRefused(string reason)
    {
        var machine = new ExecutionStateMachine();
        Assert.Throws<ArgumentException>(() => machine.MoveTo(ExecutionState.Initializing, reason));
    }

    [Fact]
    public void SubscribersSeeEachAcceptedEdgeOnce()
    {
        var machine = new ExecutionStateMachine();
        var seen = new List<ExecutionState>();
        machine.Changed += change => seen.Add(change.To);

        machine.MoveTo(ExecutionState.Initializing, "startup");
        Assert.Throws<InvalidOperationException>(() => machine.MoveTo(ExecutionState.Running, "shortcut"));

        Assert.Equal(new[] { ExecutionState.Initializing }, seen);
    }

    [Fact]
    public void ConcurrentMoversCannotBothArrive()
    {
        var machine = new ExecutionStateMachine();
        var successes = 0;

        Parallel.For(0, 32, _ =>
        {
            try
            {
                machine.MoveTo(ExecutionState.Initializing, "contender");
                Interlocked.Increment(ref successes);
            }
            catch (InvalidOperationException)
            {
                // Exactly the outcome the table is for: one edge wins, the rest are refused.
            }
        });

        Assert.Equal(1, successes);
        Assert.Equal(ExecutionState.Initializing, machine.Current);
        Assert.Single(machine.History);
    }
}
