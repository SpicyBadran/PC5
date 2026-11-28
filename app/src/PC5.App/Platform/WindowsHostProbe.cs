using System.Runtime.InteropServices;
using Microsoft.Win32;
using Pc5.Core.Diagnostics;
using Pc5.Core.Execution;
using Pc5.Core.Firmware;

namespace Pc5.App.Platform;

/// <summary>
/// The Windows half of host detection: the questions that can only be asked on a Windows
/// host, asked in a way that stays inside its own rights. Each finding says what it does not
/// prove, because a Windows capability list is not PS5 evidence and will never be quoted as
/// such. Everything here degrades to <c>null</c> (unknown) rather than to "no".
/// </summary>
public sealed class WindowsHostProbe : IHostCapabilityProbe
{
    public string Id => "windows-host";

    public async Task<HostCapabilities> DetectAsync(CancellationToken cancellationToken = default)
    {
        var portable = await new RuntimeHostProbe().DetectAsync(cancellationToken).ConfigureAwait(false);
        var findings = new List<CapabilityFinding>(portable.Findings);

        for (var index = 0; index < findings.Count; index++)
        {
            if (findings[index].Name == "virtualization-backend")
            {
                findings[index] = HypervisorSurface();
            }
            else if (findings[index].Name == "genuine-ps5-system-software-input")
            {
                findings[index] = FirmwareInput();
            }
        }

        findings.Add(GraphicsSurface());
        findings.Add(VirtualMachinePlatformRole());

        return portable with
        {
            Findings = findings,
            ProbedAtUtc = DateTimeOffset.UtcNow,
        };
    }

    /// <summary>
    /// Whether the Windows Hypervisor Platform API surface is present on this host. The
    /// check is that the platform DLL can be loaded, which is weaker than "the hypervisor can
    /// run a guest" and says so: the difference is whether the role is enabled and whether a
    /// guest would be accepted, neither of which a load test establishes.
    /// </summary>
    private static CapabilityFinding HypervisorSurface()
    {
        var loadable = false;
        try
        {
            loadable = NativeLibrary.TryLoad("WinHvPlatform.dll", typeof(WindowsHostProbe).Assembly,
                DllImportSearchPath.SafeDirectories, out var handle);
            if (loadable)
            {
                NativeLibrary.Free(handle);
            }
        }
        catch (Exception exception) when (exception is DllNotFoundException or BadImageFormatException or EntryPointNotFoundException)
        {
            loadable = false;
        }

        return new CapabilityFinding
        {
            Name = "virtualization-backend",
            Supported = loadable,
            Evidence = loadable
                ? "WinHvPlatform.dll loaded from a safe search path"
                : "WinHvPlatform.dll could not be loaded on this host",
            Meaning = loadable
                ? "the Windows Hypervisor Platform API surface exists here"
                : "no Windows hypervisor API surface was found to try",
            NotProven = "that the platform is enabled for this user, that a guest would be accepted, or that any PS5 guest requirement could be satisfied through it",
        };
    }

    /// <summary>Whether the Virtual Machine Platform Windows feature appears to be installed.</summary>
    private static CapabilityFinding VirtualMachinePlatformRole() => new()
    {
        Name = "windows-virtualization-state",
        Supported = null,
        Evidence = "not queried: reading Hyper-V role state needs a privileged query PC5 does not perform",
        Meaning = "unknown on this host by design",
        NotProven = "nothing; this finding exists to keep the absence of a measurement visible",
    };

    /// <summary>
    /// The host display adapter, read from the class key the Plug and Play manager writes. It
    /// is used to describe what PC5 will render onto. It is not a statement about guest-visible
    /// GPU behaviour: presenting a surface in a window is not executing a console GPU's command
    /// stream, and no part of this finding may be read as graphics compatibility.
    /// </summary>
    private static CapabilityFinding GraphicsSurface()
    {
        string? description = null;
        try
        {
            using var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Default)
                .OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}");
            foreach (var name in root?.GetSubKeyNames() ?? Array.Empty<string>())
            {
                if (!name.StartsWith("0", StringComparison.Ordinal))
                {
                    continue;
                }

                using var sub = root!.OpenSubKey(name);
                if (sub?.GetValue("DriverDesc") is string candidate && !string.IsNullOrWhiteSpace(candidate))
                {
                    description = candidate;
                    break;
                }
            }
        }
        catch (Exception exception) when (exception is UnauthorizedAccessException or IOException)
        {
            description = null;
        }

        return new CapabilityFinding
        {
            Name = "host-display-adapter",
            Supported = description is not null,
            Evidence = description is null
                ? "the display class key yielded no driver description for this session"
                : $"registry DriverDesc = {description}",
            Meaning = description is null
                ? "unknown"
                : $"the host's first display adapter reports itself as {description}",
            NotProven = "anything about guest graphics: no RDNA2 command stream was decoded, and a host adapter is not a guest GPU",
        };
    }

    private static CapabilityFinding FirmwareInput()
    {
        var report = FirmwareArtifactProbe.Describe(null);
        return new CapabilityFinding
        {
            Name = "genuine-ps5-system-software-input",
            Supported = report.Status == FirmwareArtifactStatus.PresentHashMatches ? true : null,
            Evidence = $"firmware probe status: {report.Status}",
            Meaning = "no authorised package has been configured for this build to verify",
            NotProven = "that any such package is obtainable lawfully, or that its contents could execute",
        };
    }
}
