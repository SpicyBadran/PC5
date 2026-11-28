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

        // Entry order is the whole point, so both entries are checked field by field. The
        // first draft of this test asserted `history[1].Reason == "startup begins"` and
        // `history[1].From == Ready`, which is what the *second* edge is not: the reason of
        // the second move is its own, and its `From` is the state the first move arrived at.
        // The compiler and the other 49 tests passed while this one failed, which is the
        // useful kind of red -- a test that was wrong about the contract it claimed to pin.
        Assert.Equal(ExecutionState.NotInitialized, history[0].From);
        Assert.Equal(ExecutionState.Initializing, history[0].To);
        Assert.Equal("startup begins", history[0].Reason);

        Assert.Equal(ExecutionState.Initializing, history[1].From);
        Assert.Equal(ExecutionState.Ready, history[1].To);
        Assert.Equal("no blocker found", history[1].Reason);

        // and the chain has to be continuous: a history that records states out of order
        // would let a log describe a lifecycle that never happened
        for (var index = 1; index < history.Count; index++)
        {
            Assert.Equal(history[index - 1].To, history[index].From);
            Assert.True(history[index].Utc >= history[index - 1].Utc,
                        $"entry {index} is stamped before the entry it follows");
        }

        Assert.True(history[1].Utc <= DateTimeOffset.UtcNow);
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
