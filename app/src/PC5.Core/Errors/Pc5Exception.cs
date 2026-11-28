namespace Pc5.Core.Errors;

/// <summary>
/// Why something did not work, in categories the UI can present without guessing.
/// <see cref="BlockedOnInput"/> is the one that must never be rendered as a crash:
/// the project not holding a file is not the user's machine being wrong.
/// </summary>
public enum Pc5ErrorCategory
{
    Configuration,
    Capability,
    BackendUnavailable,
    BlockedOnInput,
    Permission,
    Cancelled,
    InvalidTransition,
    Storage,
    Internal,
    Unknown,
}

/// <summary>A PC5 failure that carries its category and what would clear it.</summary>
public sealed class Pc5Exception : Exception
{
    public Pc5Exception(Pc5ErrorCategory category, string message, string nextStep)
        : base(message)
    {
        Category = category;
        NextStep = nextStep;
    }

    public Pc5Exception(Pc5ErrorCategory category, string message, string nextStep, Exception inner)
        : base(message, inner)
    {
        Category = category;
        NextStep = nextStep;
    }

    public Pc5ErrorCategory Category { get; }

    /// <summary>The action that would change the outcome, written for a person reading a log.</summary>
    public string NextStep { get; }

    public static Pc5Exception Blocked(string what, string why) => new(
        Pc5ErrorCategory.BlockedOnInput,
        $"{what} is not available to this build: {why}",
        "Record the blocker in docs/PROGRESS.md and continue with work that needs no such input.");

    public static Pc5Exception Capability(string capability, string evidence) => new(
        Pc5ErrorCategory.Capability,
        $"the host did not report {capability} ({evidence})",
        "Run the host-capability report on the machine that is meant to host PC5 and read its evidence column.");

    public static Pc5Exception Configuration(string problem, string expected) => new(
        Pc5ErrorCategory.Configuration,
        $"configuration is not usable: {problem}",
        $"expected: {expected}");
}
