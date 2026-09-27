
## Cluster upgrade requirement: recoverable session stop

Upgrade the controller and every worker together before using remote session
stop with this release. The controller requires the worker's
`terrarium.session/unload` operation to preserve saved creature IDs and Drive
ownership. Mixed deployments with older workers do not support this stop path.

An unsupported operation, timeout, or lost reply causes stop to report failure.
The saved dormant marker remains in place; it does not prove that the worker has
stopped. Upgrade the worker and retry stopping the same session. The controller
never falls back to permanent creature deletion, which would orphan Drives.

See the [Drive identity recovery guide](https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/docs/en/guides/drive-identity-recovery.md)
for existing orphaned goals and offline repair.
