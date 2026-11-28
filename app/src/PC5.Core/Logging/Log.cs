using System.Text;

namespace Pc5.Core.Logging;

public enum LogLevel { Trace, Debug, Info, Warn, Error }

/// <summary>
/// PC5's logging seam. Every entry carries an event id as well as text, because the
/// evidence a reviewer attaches to a report is a sequence of events, not prose that can
/// drift. Log lines never claim more than the code that emitted them knows.
/// </summary>
public interface ILog
{
    void Write(LogLevel level, string eventId, string message);
}

public static class LogExtensions
{
    public static void Trace(this ILog log, string id, string message) => log.Write(LogLevel.Trace, id, message);
    public static void Debug(this ILog log, string id, string message) => log.Write(LogLevel.Debug, id, message);
    public static void Info(this ILog log, string id, string message) => log.Write(LogLevel.Info, id, message);
    public static void Warn(this ILog log, string id, string message) => log.Write(LogLevel.Warn, id, message);
    public static void Error(this ILog log, string id, string message) => log.Write(LogLevel.Error, id, message);
}

/// <summary>
/// An append-only UTF-8 log file with an in-process level filter. Writes are serialised,
/// so a lifecycle that touches logging from a continuation cannot interleave half a line.
/// A log that cannot be written is reported through the callback rather than swallowed:
/// a build that silently loses its diagnostics has lost its evidence too.
/// </summary>
public sealed class FileLog : ILog, IDisposable
{
    private static readonly string[] LevelNames = { "TRACE", "DEBUG", "INFO ", "WARN ", "ERROR" };

    private readonly object Gate = new();
    private readonly LogLevel minimum;
    private StreamWriter? writer;
    private bool disposed;

    public FileLog(string path, LogLevel minimum = LogLevel.Info, Action<string>? onWriteFailure = null)
    {
        Path = path;
        this.minimum = minimum;
        WriteFailure = onWriteFailure;
    }

    public string Path { get; }

    /// <summary>Lines refused by the level filter, counted so a quiet log can be explained.</summary>
    public long Suppressed { get; private set; }

    public Action<string>? WriteFailure { get; }

    public void Write(LogLevel level, string eventId, string message)
    {
        if (string.IsNullOrWhiteSpace(eventId))
        {
            throw new ArgumentException("every PC5 log entry carries an event id", nameof(eventId));
        }

        if (level < minimum)
        {
            lock (Gate) { Suppressed++; }
            return;
        }

        if (disposed)
        {
            return;
        }

        var line = $"{DateTimeOffset.UtcNow:O} {LevelNames[(int)level]} [{eventId.Trim()}] {message}";
        lock (Gate)
        {
            try
            {
                writer ??= Open();
                writer.WriteLine(line);
                writer.Flush();
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                writer = null;
                WriteFailure?.Invoke($"log write to {Path} failed: {exception.Message}");
            }
        }
    }

    /// <summary>The whole file, for the diagnostics view. Returns nothing before the first write.</summary>
    public string Tail(int maximumLines = 200)
    {
        lock (Gate)
        {
            writer?.Flush();
            if (!System.IO.File.Exists(Path))
            {
                return string.Empty;
            }

            var lines = new Queue<string>();
            foreach (var line in System.IO.File.ReadLines(Path))
            {
                lines.Enqueue(line);
                while (lines.Count > maximumLines)
                {
                    lines.Dequeue();
                }
            }

            return string.Join(Environment.NewLine, lines);
        }
    }

    private StreamWriter Open()
    {
        var directory = System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(Path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        // `Encoding` is a constructor argument here, not an initialiser: `StreamWriter.Encoding`
        // is read-only (CS0200, reported by the first hosted compile), and the explicit
        // UTF8Encoding is what keeps a BOM out of a file that is appended to for its life.
        return new StreamWriter(
            new FileStream(Path, FileMode.Append, FileAccess.Write, FileShare.Read),
            new UTF8Encoding(encoderShouldEmitUTF8Identifier: false))
        {
            AutoFlush = false,
        };
    }

    public void Dispose()
    {
        lock (Gate)
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            writer?.Dispose();
            writer = null;
        }
    }
}

/// <summary>An in-memory sink, for tests and for the application's own diagnostics pane.</summary>
public sealed class MemoryLog : ILog
{
    private readonly List<string> lines = new();

    public IReadOnlyList<string> Lines
    {
        get { lock (lines) { return lines.ToArray(); } }
    }

    public void Write(LogLevel level, string eventId, string message)
    {
        lock (lines)
        {
            lines.Add($"{level} [{eventId}] {message}");
        }
    }

    public bool Contains(string fragment)
    {
        lock (lines)
        {
            return lines.Any(line => line.Contains(fragment, StringComparison.Ordinal));
        }
    }
}
