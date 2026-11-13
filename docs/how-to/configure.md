# Configure a placed block instance

A configuration belongs to one facade symbol instance. Changing it does not edit the shared library or other placed instances.

## Configure before placement

1. Open **Browse and Place Functional Blocks**.
2. Select the block.
3. Choose the required implementation variant.
4. Choose a named profile if the block defines one.
5. Place the facade using KiCad's native chooser.

The details pane describes the available settings and configurations. A profile can set a variant, typed parameter overrides, and a PCB layout in one selection.

## Configure after placement

1. Select exactly one placed facade symbol in Eeschema.
2. Open **KiCad Blocks Tasks → Configure selected instance**.
3. Choose a named profile or edit the variant, parameter values, and PCB layout.
4. Save the configuration.

The configuration is stored in KiCad symbol fields on that instance. You can also inspect or edit the fields through KiCad's **Symbol Properties → Fields** page.

## Understand the fields

| Field | Purpose |
| --- | --- |
| `KB_VARIANT` | Selects an alternative implementation schematic. |
| `KB_PROFILE` | Records the selected named profile ID. |
| `KB_LAYOUT` | Selects the associated PCB layout ID. |
| `KB_PARAM_<NAME>` | Stores a typed override for a declared block parameter. |

Changing a field that is linked to a profile makes the configuration custom and clears `KB_PROFILE`. Matching variant names are case-insensitive but otherwise exact. If a block defines variants, an empty or unknown value is an error; the plugin does not guess which implementation you intended.

Parameters support text, number, integer, and boolean values. Tokens in supported quoted implementation text fields are expanded for recursive BOM/ERC processing. Parameter values do not evaluate arbitrary expressions, alter connectivity, or dynamically reconfigure the top-level schematic.

See [per-instance fields](../reference.md#per-instance-fields), [parameters](../reference.md#parameters-and-token-expansion), and [variants and profiles](../reference.md#variants-and-profiles).
