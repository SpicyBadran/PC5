using Pc5.Core.Firmware;
using Xunit;

namespace Pc5.Core.Tests;

/// <summary>
/// The firmware-integrity logic the PUP investigation depends on. The repository's own
/// pointer text is the fixture: 135 bytes naming an object that is not there. A test that
/// only checked "a file exists" would be the mistake this code exists to prevent.
/// </summary>
public class FirmwareArtifactTests : IDisposable
{
    private const string RepositoryPointer =
        "version https://git-lfs.github.com/spec/v1\n" +
        "oid sha256:42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb\n" +
        "size 1423697408\n";

    private readonly string directory = Path.Combine(Path.GetTempPath(), "pc5-fw-" + Guid.NewGuid().ToString("N"));

    [Fact]
    public void TheRepositoryPointerGrammarIsRecognisedExactly()
    {
        Assert.True(LfsPointer.TryParse(RepositoryPointer, out var pointer, out var error));
        Assert.Null(error);
        Assert.Equal("42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb", pointer!.Sha256Oid);
        Assert.Equal(1_423_697_408L, pointer.Size);
    }

    [Theory]
    [InlineData("version https://git-lfs.github.com/spec/v1\nsize 10\n")]
    [InlineData("version https://git-lfs.github.com/spec/v1\noid sha256:deadbeef\nsize 10\n")]
    [InlineData("version https://git-lfs.github.com/spec/v2\noid sha256:aaa\nsize 10\n")]
    [InlineData("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")]
    [InlineData("not a pointer at all")]
    public void AnythingElseIsNotAPointer(string text)
    {
        Assert.False(LfsPointer.TryParse(text, out var pointer, out var error));
        Assert.Null(pointer);
        Assert.False(string.IsNullOrWhiteSpace(error));
    }

    [Fact]
    public void APointerFileIsReportedAsPointerOnlyAndNeverAsFirmware()
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "PS5UPDATE.PUP");
        File.WriteAllText(path, RepositoryPointer);

        var report = FirmwareArtifactProbe.Describe(path);

        Assert.Equal(FirmwareArtifactStatus.PointerOnly, report.Status);
        Assert.Equal(1423697408L, report.DeclaredPayloadBytes!.Value);
        Assert.Equal(new FileInfo(path).Length, report.ReadBytes!.Value);
        Assert.Null(report.ComputedSha256);
        Assert.False(report.DeclaresGenuineFirmware);
        Assert.Contains("not present here", report.Detail!, StringComparison.Ordinal);
        Assert.Contains("nothing here indicates", report.ToText(), StringComparison.Ordinal);
    }

    [Fact]
    public void AMissingFileIsAbsentAndSaysSoWithoutSpeculation()
    {
        var report = FirmwareArtifactProbe.Describe(Path.Combine(directory, "nope.PUP"));
        Assert.Equal(FirmwareArtifactStatus.Absent, report.Status);
        Assert.Contains("no file at the configured path", report.Detail!, StringComparison.Ordinal);
        Assert.Contains("not proven", report.ToText(), StringComparison.Ordinal);
        Assert.Contains("not configured", FirmwareArtifactProbe.Describe(null).ToText(), StringComparison.Ordinal);
    }

    [Fact]
    public void APayloadIsHashedButItsVersionIsStillNotAsserted()
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "candidate.PUP");
        var payload = new byte[4096];
        new byte[] { 0x53, 0x4C, 0x42, 0x32 }.CopyTo(payload, 0);
        File.WriteAllBytes(path, payload);

        var report = FirmwareArtifactProbe.Describe(path);

        Assert.Equal(FirmwareArtifactStatus.PresentUncompared, report.Status);
        Assert.Equal("SLB2", report.ContainerMagic);
        Assert.Equal(64, report.ComputedSha256!.Length);
        Assert.Contains("version", report.NotProven, StringComparison.Ordinal);
    }

    [Fact]
    public void BytesThatAreNotTheDeclaredObjectAreNamedAsUnrelated()
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "other.PUP");
        File.WriteAllBytes(path, new byte[] { 1, 2, 3, 4 });

        var declared = new LfsPointer("42ea0e5aad1361e098837d40157863c4c519f0bbc66eb03c04dbaaf339b063fb", 1423697408);
        var report = FirmwareArtifactProbe.Describe(path, declared);

        Assert.Equal(FirmwareArtifactStatus.PresentHashDiffers, report.Status);
        Assert.Contains("unrelated file", report.Detail!, StringComparison.Ordinal);
        Assert.Equal(declared.Sha256Oid, report.DeclaredSha256);
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(directory))
            {
                System.IO.Directory.Delete(directory, recursive: true);
            }
        }
        catch (IOException)
        {
        }
    }
}
