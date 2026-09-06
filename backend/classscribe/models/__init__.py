"""Model installation and registry primitives without model-library imports."""

from classscribe.models.download import HuggingFaceDownloader
from classscribe.models.environment import WorkerEnvironmentProvisioner
from classscribe.models.health import InstalledModelHealthChecker
from classscribe.models.inference import SandboxedModelInvoker
from classscribe.models.licenses import ModelLicense, load_model_licenses
from classscribe.models.manager import (
    DownloadReceipt,
    HealthCheckOutcome,
    InstallationPlan,
    InstallationResult,
    ManifestComponentSource,
    ManifestComponentSourceFile,
    ManifestDiff,
    ManifestFile,
    ModelManager,
    ModelManifest,
    SQLAlchemyInstallationRecorder,
    runtime_environment,
)
from classscribe.models.manifests import (
    LoadedManifestBundle,
    ManifestBundleEntry,
    ManifestBundleGenerator,
    ManifestBundleIndex,
    load_builtin_manifest_bundle,
    load_manifest_bundle,
)
from classscribe.models.registry import ModelEntry, ModelRegistry, RegistryStore, load_registry
from classscribe.models.resident import DictationWorkerSupervisor
from classscribe.models.worker_process import (
    WorkerProcess,
    WorkerProcessSpec,
    provisioned_worker_command,
    worker_command,
)

__all__ = [
    "DictationWorkerSupervisor",
    "DownloadReceipt",
    "HealthCheckOutcome",
    "HuggingFaceDownloader",
    "InstallationPlan",
    "InstallationResult",
    "InstalledModelHealthChecker",
    "LoadedManifestBundle",
    "ManifestBundleEntry",
    "ManifestBundleGenerator",
    "ManifestBundleIndex",
    "ManifestComponentSource",
    "ManifestComponentSourceFile",
    "ManifestDiff",
    "ManifestFile",
    "ModelEntry",
    "ModelLicense",
    "ModelManager",
    "ModelManifest",
    "ModelRegistry",
    "RegistryStore",
    "SQLAlchemyInstallationRecorder",
    "SandboxedModelInvoker",
    "WorkerEnvironmentProvisioner",
    "WorkerProcess",
    "WorkerProcessSpec",
    "load_builtin_manifest_bundle",
    "load_manifest_bundle",
    "load_model_licenses",
    "load_registry",
    "provisioned_worker_command",
    "runtime_environment",
    "worker_command",
]
