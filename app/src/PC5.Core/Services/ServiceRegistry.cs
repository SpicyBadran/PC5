using System.Collections.Concurrent;
using Pc5.Core.Errors;

namespace Pc5.Core.Services;

/// <summary>
/// The application's service boundary, without a container dependency.
///
/// Registration is first-write-wins: two components claiming the same contract is the shape
/// of duplicated subsystem ownership, and PC5's rules forbid it quietly. Resolving something
/// unregistered is a programming error and says so, rather than handing back null.
/// </summary>
public sealed class ServiceRegistry
{
    private readonly ConcurrentDictionary<Type, object> services = new();

    public IReadOnlyList<string> Registered => services.Keys.Select(t => t.Name).OrderBy(n => n, StringComparer.Ordinal).ToArray();

    public void Register<T>(T service)
        where T : class
    {
        if (service is null)
        {
            throw new ArgumentNullException(nameof(service));
        }

        if (!services.TryAdd(typeof(T), service))
        {
            throw new Pc5Exception(
                Pc5ErrorCategory.Internal,
                $"service {typeof(T).Name} is already registered by {service.GetType().Name}",
                "one contract, one owner: remove the duplicate registration rather than the first one");
        }
    }

    public T Resolve<T>()
        where T : class
    {
        if (services.TryGetValue(typeof(T), out var service))
        {
            return (T)service;
        }

        throw new Pc5Exception(
            Pc5ErrorCategory.Internal,
            $"no service registered for {typeof(T).Name}",
            "register it during initialization; PC5 does not substitute a stand-in for a missing service");
    }

    public bool TryResolve<T>(out T? service)
        where T : class
    {
        if (services.TryGetValue(typeof(T), out var found))
        {
            service = (T)found;
            return true;
        }

        service = null;
        return false;
    }
}
