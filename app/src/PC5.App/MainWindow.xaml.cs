using Microsoft.UI.Xaml;
using Pc5.App.ViewModels;

namespace Pc5.App;

/// <summary>
/// The window. It contains no lifecycle rule, no capability judgement and no claim of its
/// own: every string it prints comes from the view model, which gets it from PC5.Core. The
/// one decision made here is that a failing operation is shown as text rather than as a
/// silent no-op, because a diagnostics tool that fails quietly is not a diagnostics tool.
/// </summary>
public sealed partial class MainWindow : Window
{
    private readonly LifecycleViewModel viewModel;

    public MainWindow(HostComposition host)
    {
        InitializeComponent();
        viewModel = host.ViewModel;
        viewModel.Changed += () => DispatcherQueue.TryEnqueue(Redraw);
        Subtitle.Text = viewModel.Title;
        Redraw();
    }

    private void Redraw()
    {
        LifecycleLine.Text = $"state: {viewModel.State}";
        ClaimLine.Text = $"this build may claim: {Pc5.Core.Versioning.BuildInfo.BuildEvidenceCeiling} " +
                        $"(application builds). {Pc5.Core.Versioning.BuildInfo.TruthStatement}.";
        FirmwareLine.Text = $"firmware input: {viewModel.Firmware.Status}" +
                            (viewModel.Firmware.DeclaredPayloadBytes is { } size ? $" (declared {size} bytes)" : string.Empty);
        History.Text = string.Join(Environment.NewLine,
            viewModel.History.Select(change => $"{change.Utc:HH:mm:ss.fff}  {change.From} -> {change.To}  ({change.Reason})"));
        Log.Text = viewModel.LogTail();
    }

    private async void OnProbe(object sender, RoutedEventArgs e)
    {
        Capabilities.Text = await Run("reading host capabilities", viewModel.RefreshCapabilitiesAsync);
    }

    private async void OnStart(object sender, RoutedEventArgs e)
    {
        Capabilities.Text = await Run("starting the test backend", viewModel.StartTestBackendAsync);
        Redraw();
    }

    private async void OnStop(object sender, RoutedEventArgs e)
    {
        Capabilities.Text = await Run("stopping", viewModel.StopTestBackendAsync);
        Redraw();
    }

    private async void OnSaveSettings(object sender, RoutedEventArgs e)
    {
        Capabilities.Text = await Run("writing settings", () => Task.Run(() =>
        {
            new Pc5.Core.Configuration.SettingsStore(
                System.IO.Path.GetDirectoryName(Pc5.Core.Configuration.Pc5Paths.SettingsFile())).Save(viewModel.Settings);
            return $"settings written for backend {viewModel.Settings.Backend}";
        }));
        Redraw();
    }

    private void OnExit(object sender, RoutedEventArgs e) => Close();

    /// <summary>Runs an operation and returns either its result or the refusal, as text.</summary>
    private static async Task<string> Run(string what, Func<Task<string>> operation)
    {
        try
        {
            return await operation();
        }
        catch (Exception exception)
        {
            return $"{what} did not complete: {exception.Message}";
        }
    }

    private static string Run(string what, Func<string> operation)
    {
        try
        {
            return operation();
        }
        catch (Exception exception)
        {
            return $"{what} did not complete: {exception.Message}";
        }
    }
}
