using System.Text.Json;
using System.Text.Json.Serialization;
using Pc5.Core.Errors;
using Pc5.Core.Logging;

namespace Pc5.Core.Configuration;

/// <summary>Where PC5 keeps its files, resolved the same way for every caller.</summary>
public static class Pc5Paths
{
    /// <summary>
    /// PC5_HOME wins, so a test or a portable install can point the whole application at a
    /// scratch directory. Otherwise the per-user local application data directory, which on
    /// Windows is %LOCALAPPDATA% and on other hosts its equivalent. PC5 never writes next to
    /// its own executable, and never into a Windows system directory.
    /// </summary>
    public static string Root()
    {
        var configured = Environment.GetEnvironmentVariable("PC5_HOME");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return System.IO.Path.GetFullPath(configured);
        }

        var local = Environment.GetFolderPath(
            Environment.SpecialFolder.LocalApplicationData,
            Environment.SpecialFolderOption.Create);
        return System.IO.Path.Combine(local, "PC5");
    }

    public static string SettingsFile() => System.IO.Path.Combine(Root(), "settings.json");

    public static string LogFile() => System.IO.Path.Combine(Root(), "logs", "pc5.log");

    public static string ReportsDirectory() => System.IO.Path.Combine(Root(), "reports");
}

/// <summary>
/// Reads and writes <see cref="Pc5Settings"/> as JSON on disk.
///
/// Save is atomic (temp file then move), so a power loss mid-write cannot leave a
/// half-written settings file that the next launch reads as configuration. A file that
/// cannot be parsed is preserved with a .bad suffix and reported: the application starts on
/// defaults, and the log says that it did, because quietly discarding someone's settings is
/// how a diagnostics tool loses the right to be trusted.
/// </summary>
public sealed class SettingsStore
{
    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    private readonly ILog? log;

    public SettingsStore(string? directory = null, ILog? log = null)
    {
        DirectoryPath = directory ?? Pc5Paths.Root();
        this.log = log;
    }

    /// <summary>The directory holding settings.json. Named to avoid shadowing System.IO.Directory.</summary>
    public string DirectoryPath { get; }

    public string FilePath => System.IO.Path.Combine(DirectoryPath, "settings.json");

    public Pc5Settings Load()
    {
        if (!File.Exists(FilePath))
        {
            log?.Debug("cfg.001", $"no settings file at {FilePath}; using defaults");
            return new Pc5Settings();
        }

        Pc5Settings? loaded;
        try
        {
            loaded = JsonSerializer.Deserialize<Pc5Settings>(File.ReadAllText(FilePath), Json);
        }
        catch (JsonException exception)
        {
            var quarantined = FilePath + ".bad";
            try
            {
                if (File.Exists(quarantined))
                {
                    File.Delete(quarantined);
                }

                File.Move(FilePath, quarantined);
            }
            catch (IOException)
            {
                // Quarantine is best-effort; the refusal below is the part that matters.
            }

            log?.Error("cfg.002", $"settings file unreadable ({exception.Message}); defaults used, original kept at {quarantined}");
            throw new Pc5Exception(
                Pc5ErrorCategory.Configuration,
                $"the settings file at {FilePath} is not valid JSON: {exception.Message}",
                $"fix or delete {quarantined}, then re-run; PC5 started on defaults and changed nothing",
                exception);
        }

        if (loaded is null)
        {
            log?.Warn("cfg.003", "settings file parsed as null; defaults used");
            return new Pc5Settings();
        }

        var problems = loaded.Problems();
        foreach (var problem in problems)
        {
            log?.Warn("cfg.004", $"settings problem: {problem}");
        }

        return loaded;
    }

    public void Save(Pc5Settings settings)
    {
        var problems = settings.Problems();
        if (problems.Count > 0)
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.Configuration,
                "refused to write settings that this build would not accept: " + string.Join("; ", problems),
                "correct the values; PC5 does not persist configuration it would have to reject on load");
        }

        System.IO.Directory.CreateDirectory(DirectoryPath);
        var temporary = FilePath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(settings, Json));
        File.Move(temporary, FilePath, overwrite: true);
        log?.Info("cfg.005", $"settings written to {FilePath}");
    }
}
