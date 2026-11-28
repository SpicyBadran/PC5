using System.Security.Cryptography;

namespace Pc5.Core.Firmware;

/// <summary>
/// How PC5 can honestly describe a firmware artifact it has been pointed at.
///
/// The first three cases are the interesting ones, because the repository's own
/// `PS5UPDATE.PUP` is <see cref="PointerOnly"/>: a 135-byte Git LFS pointer naming an
/// object the store does not hold. Reporting that as "firmware present" is the exact
/// mistake this type exists to make impossible.
/// </summary>
public enum FirmwareArtifactStatus
{
    /// <summary>No file at the configured path.</summary>
    Absent,

    /// <summary>The file is a Git LFS pointer; the payload it names is not here.</summary>
    PointerOnly,

    /// <summary>A payload exists but could not be read (permissions, sharing, IO error).</summary>
    Unreadable,

    /// <summary>A payload was hashed, with nothing declared to compare it against.</summary>
    PresentUncompared,

    /// <summary>A payload was hashed; it is not the object the repository's pointer declares.</summary>
    PresentHashDiffers,

    /// <summary>A payload was hashed and matches the declared object id.</summary>
    PresentHashMatches,
}

/// <summary>A parsed Git LFS pointer (the text a pointer file contains).</summary>
public sealed record LfsPointer(string Sha256Oid, long Size)
{
    public const string VersionLine = "version https://git-lfs.github.com/spec/v1";

    /// <summary>
    /// Parse the pointer grammar and nothing else: a version line, an `oid sha256:` of 64
    /// hex digits, and a decimal `size`. A file that merely mentions those words is not a
    /// pointer, so both the leading marker and the exact field shapes are required.
    /// </summary>
    public static bool TryParse(string text, out LfsPointer? pointer, out string? error)
    {
        pointer = null;
        error = null;

        var lines = (text ?? string.Empty).Replace("\r\n", "\n").Split('\n');
        if (lines.Length == 0 || !lines[0].TrimEnd().Equals(VersionLine, StringComparison.Ordinal))
        {
            error = "first line is not the git-lfs pointer version line";
            return false;
        }

        string? oid = null;
        long size = -1;
        foreach (var raw in lines.Skip(1))
        {
            var line = raw.Trim();
            if (line.StartsWith("oid sha256:", StringComparison.Ordinal))
            {
                oid = line["oid sha256:".Length..].Trim();
            }
            else if (line.StartsWith("size ", StringComparison.Ordinal))
            {
                if (!long.TryParse(line["size ".Length..].Trim(), out size))
                {
                    error = $"size is not a decimal integer: {line}";
                    return false;
                }
            }
        }

        if (oid is null)
        {
            error = "no 'oid sha256:' line";
            return false;
        }

        if (oid.Length != 64 || !oid.All(Uri.IsHexDigit))
        {
            error = $"sha256 oid is not 64 hex digits (found {oid.Length} characters)";
            return false;
        }

        if (size < 0)
        {
            error = "no 'size <bytes>' line";
            return false;
        }

        pointer = new LfsPointer(oid.ToLowerInvariant(), size);
        return true;
    }

    public override string ToString() => $"sha256:{Sha256Oid} size={Size}";
}

/// <summary>What a firmware probe established, and what it refused to conclude.</summary>
public sealed record FirmwareArtifactReport
{
    public required string Path { get; init; }
    public required FirmwareArtifactStatus Status { get; init; }
    public string? Detail { get; init; }

    /// <summary>Bytes on disk that PC5 actually read. For a pointer, the pointer's own length.</summary>
    public long? ReadBytes { get; init; }

    /// <summary>The size the pointer declares, when this is a pointer. Not a measurement of payload.</summary>
    public long? DeclaredPayloadBytes { get; init; }

    public string? ComputedSha256 { get; init; }
    public string? DeclaredSha256 { get; init; }

    /// <summary>The first four bytes, for the `SLB2` container marker public reports describe.</summary>
    public string? ContainerMagic { get; init; }

    /// <summary>What this report does not establish. Always present.</summary>
    public required string NotProven { get; init; }

    public bool DeclaresGenuineFirmware => false;

    public string ToText()
    {
        var lines = new List<string>
        {
            $"path            : {Path}",
            $"status          : {Status}",
        };
        if (Detail is not null) lines.Add($"detail          : {Detail}");
        if (ReadBytes is { } read) lines.Add($"bytes read      : {read}");
        if (DeclaredPayloadBytes is { } declared)
        {
            lines.Add($"declared payload: {declared} bytes (a pointer's claim, not a measurement)");
        }

        if (ComputedSha256 is not null) lines.Add($"sha256          : {ComputedSha256}");
        if (DeclaredSha256 is not null) lines.Add($"declared oid    : {DeclaredSha256}");
        if (ContainerMagic is not null) lines.Add($"container magic : {ContainerMagic}");
        lines.Add($"not proven      : {NotProven}");
        lines.Add("A PUP is a system-software package. It is not a bootable image on its own,");
        lines.Add("and nothing here indicates that any component inside it can be executed.");
        return string.Join(Environment.NewLine, lines);
    }
}

/// <summary>
/// The integrity side of PC5's firmware research: identify, hash, and state limits.
/// It parses structure and computes digests. It never decrypts, never modifies, never
/// copies a package into the repository, and never infers a version from a file size.
/// </summary>
public static class FirmwareArtifactProbe
{
    /// <summary>The SLB2 outer-container marker public format reports describe.</summary>
    public static readonly ReadOnlyMemory<byte> Slb2Magic = new byte[] { 0x53, 0x4C, 0x42, 0x32 };

    public static FirmwareArtifactReport Describe(string? path, LfsPointer? declared = null)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return new FirmwareArtifactReport
            {
                Path = "(not configured)",
                Status = FirmwareArtifactStatus.Absent,
                Detail = "no firmware path is configured in PC5's settings",
                NotProven = "that any package exists, or that one would be usable",
            };
        }

        if (!File.Exists(path))
        {
            return new FirmwareArtifactReport
            {
                Path = path,
                Status = FirmwareArtifactStatus.Absent,
                Detail = "no file at the configured path",
                NotProven = "that the package is anywhere reachable, or that it exists at all",
            };
        }

        byte[] head;
        long length;
        string sha256;
        try
        {
            length = new FileInfo(path).Length;
            using var read = File.OpenRead(path);
            head = new byte[Math.Min(4, length)];
            _ = read.Read(head);
            read.Position = 0;
            sha256 = Convert.ToHexString(SHA256.HashData(read)).ToLowerInvariant();
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return new FirmwareArtifactReport
            {
                Path = path,
                Status = FirmwareArtifactStatus.Unreadable,
                Detail = exception.Message,
                NotProven = "anything: the file was not read",
            };
        }

        var text = TryReadText(path, length);
        if (text is not null && LfsPointer.TryParse(text, out var pointer, out _) && pointer is not null)
        {
            var matchesDeclared = declared is null || pointer.Sha256Oid == declared.Sha256Oid;
            return new FirmwareArtifactReport
            {
                Path = path,
                Status = FirmwareArtifactStatus.PointerOnly,
                Detail = matchesDeclared
                    ? "a Git LFS pointer naming an object that is not present here"
                    : $"a Git LFS pointer that does not match the repository's declared object ({declared})",
                ReadBytes = length,
                DeclaredPayloadBytes = pointer.Size,
                DeclaredSha256 = pointer.Sha256Oid,
                NotProven = "that the named object exists in any store, or that it could ever be retrieved",
            };
        }

        var magic = head.Length == Slb2Magic.Length
            ? System.Text.Encoding.ASCII.GetString(head.ToArray())
            : null;
        var looksLikeSlb2 = magic is not null && magic == "SLB2";
        var status = declared is null
            ? FirmwareArtifactStatus.PresentUncompared
            : sha256 == declared.Sha256Oid
                ? FirmwareArtifactStatus.PresentHashMatches
                : FirmwareArtifactStatus.PresentHashDiffers;

        return new FirmwareArtifactReport
        {
            Path = path,
            Status = status,
            Detail = status switch
            {
                FirmwareArtifactStatus.PresentHashDiffers =>
                    "the bytes here are not the bytes the repository's pointer declares; treat them as an unrelated file",
                FirmwareArtifactStatus.PresentUncompared =>
                    "a payload was read and hashed; nothing was declared to compare it against",
                _ => $"a payload was read and hashed; the outer container marker is {(looksLikeSlb2 ? "the reported SLB2" : magic ?? "unreadable")}",
            },
            ReadBytes = length,
            ComputedSha256 = sha256,
            DeclaredSha256 = declared?.Sha256Oid,
            DeclaredPayloadBytes = declared?.Size,
            ContainerMagic = magic ?? $"(short: {head.Length} bytes)",
            NotProven = "package version, completeness, authenticity, or that anything inside it can execute",
        };
    }

    /// <summary>
    /// A pointer is small text; a payload is not. Reading at most 4 KiB keeps this probe from
    /// pulling a gigabyte package into memory just to decide which of the two it is.
    /// </summary>
    private static string? TryReadText(string path, long length)
    {
        if (length is <= 0 or > 4096)
        {
            return null;
        }

        try
        {
            return File.ReadAllText(path);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }
}
