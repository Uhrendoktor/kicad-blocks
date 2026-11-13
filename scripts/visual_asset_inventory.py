"""Canonical visual-documentation bundle inventory.

The capture fixture and publisher import this one set so that adding a new
rendered UI surface cannot silently make preview publication reject CI output.
"""

ASSET_NAMES = frozenset(
    {
        "block-picker-initial.png",
        "block-picker-filtered.png",
        "block-picker-profile.png",
        "block-picker-workflow.gif",
        "library-browser-light.png",
        "library-browser-dark.png",
        "library-browser-details-light.png",
        "library-browser-details-dark.png",
        "library-folders-light.png",
        "library-folders-dark.png",
        "task-hub-light.png",
        "task-hub-dark.png",
        "task-hub-edit-maintain-light.png",
        "task-hub-edit-maintain-dark.png",
        "task-hub-checks-pcb-light.png",
        "task-hub-checks-pcb-dark.png",
        "instance-configuration-light.png",
        "instance-configuration-dark.png",
        "implementation-variants-light.png",
        "implementation-variants-dark.png",
        "implementation-details-light.png",
        "implementation-details-dark.png",
        "create-block-form-light.png",
        "create-block-form-dark.png",
        "functional-block-facade.svg",
        "functional-block-implementation.svg",
        "functional-block-implementation.kicad_sch",
    }
)
