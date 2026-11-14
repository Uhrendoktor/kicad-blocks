# Contribute to KiCad Blocks

Contributions are welcome for code, tests, examples, and documentation. Start with an issue for a substantial change, then open a pull request that explains the problem, the chosen approach, and the evidence used to validate it.

## Documentation structure

The documentation uses four complementary forms, based on the [Diátaxis documentation system](https://diataxis.fr/):

| Form | Reader question | Put this content there |
| --- | --- | --- |
| Tutorial | "Can you guide me through a first success?" | One concrete, end-to-end learning path with a known result. |
| How-to | "How do I complete this task?" | Focused procedures for readers who already know the basics. |
| Explanation | "Why does it work this way?" | Architecture, concepts, constraints, and trade-offs. |
| Reference | "What exactly does this field or action do?" | Complete, accurate descriptions of actions, data, settings, and behavior. |

Keep the four forms distinct. A tutorial should not become a catalogue of options. A how-to should not turn into a conceptual essay. Reference material should describe behavior, not speculate or teach a sequence. Explanations should not hide required procedures.

## Writing conventions

Use the following conventions when adding or changing documentation:

- Use sentence case and descriptive headings. Prefer task verbs for task pages and noun phrases for concepts.
- State the audience, starting context, and expected result near the start of a guide.
- Write procedures as numbered steps. Use one imperative action per step, preserve the exact visible KiCad action/button labels, and say which editor or dialog the reader is using.
- Keep paragraphs focused. Put the important term or result near the start so readers can scan the page.
- Use code blocks for copyable commands and configuration. State the working directory and supported version where that affects the command.
- Use tables for compact comparisons or stable reference data, not for long procedural sequences.
- Use descriptive link text that makes sense when read out of context.
- Give every informative image concise alt text. Captions and surrounding text must explain any fact that would otherwise exist only in the image.
- Distinguish tested behavior from installer acceptance, planned work, and untested platform compatibility. Never infer support from a version parser accepting a value.
- Date claims about current KiCad releases and link to the primary KiCad source.

Prefer direct, literal wording. Avoid subjective filler such as "simply", "obviously", "quickly", or "properly". Use the exact product terms **facade**, **implementation**, **variant**, **profile**, **local copy**, and **PCB layout** consistently.

## Accessible visual evidence

Screenshots and recordings should explain a real application state or interaction, not replace the procedure or the underlying technical information.

- Give every informative image concise alt text that describes its purpose. Put detailed context, caveats, and important values in nearby text.
- Introduce each figure with a complete sentence. Explain the user action and the resulting state; do not assume that the image alone communicates the workflow.
- For a GIF that demonstrates a meaningful interaction, link to representative still frames or provide a text description of the state changes. Avoid flashing effects and rapid transitions.
- Do not encode pin identity, electrical meaning, success, or error state through color alone. Pair color with labels, symbols, or text.
- Prefer KiCad-generated SVG for schematic and symbol renders when available. Use PNG for genuine application screenshots; do not use raster images of text, code, or terminal output.
- Capture the real application at a readable size. Correct the UI or capture fixture and regenerate the evidence instead of retouching a generated screenshot.

These rules follow Google's [accessible documentation guidance](https://developers.google.com/style/accessibility) and [image guidance](https://developers.google.com/style/images).

## Keep technical claims current

Compatibility pages and installation instructions contain facts that can change independently of the plugin code.

- Date claims about current stable releases and identify development nightlies as development builds.
- Link version-specific claims to the primary KiCad release notes, download pages, or API documentation.
- Distinguish a version accepted by the installer from a version covered by the full integration suite.
- Record the tested operating system, architecture, Python version, and KiCad interfaces separately when their coverage differs.
- Recheck external links and platform-specific paths when their related dependency or KiCad release changes.

Treat an undated statement about the latest release as temporary. Prefer describing the tested capability boundary and adding a dated observation when the exact current version matters.

## Update documentation with behavior

When a code change changes a user-visible action, dialog, field, workflow, compatibility boundary, or error condition, update the relevant documentation in the same pull request. Update the action map and screenshots when UI labels or screen layouts change.

Keep one canonical home for each detailed topic. Other pages should summarize the relevant point and link to the canonical section rather than maintaining competing copies of the same reference.

## Validate documentation changes

Run the documentation checks from the repository root:

~~~bash
python scripts/validate_docs.py
~~~

The check validates relative Markdown and HTML links, local heading anchors, and non-empty alt text for Markdown and HTML images. It does not attempt to fetch external websites, so external links still need to be chosen carefully.

When changing the UI, regenerate the visual bundle:

~~~bash
xvfb-run -a -s "-screen 0 1600x1000x24" \
  python tests/capture_visual_docs.py --output-dir artifacts/visual-docs
~~~

CI runs the documentation check, unit tests, lint, KiCad validation, integration tests, and visual capture. Review the rendered screenshots rather than assuming that a passing source-level test proves a dialog is readable.

## Research behind the documentation approach

The structure and writing conventions draw on these primary community and platform references:

- [Diátaxis](https://diataxis.fr/) — separates tutorials, how-to guides, explanation, and reference by reader need.
- [Write the Docs: documentation principles](https://www.writethedocs.org/guide/writing/docs-principles/) — emphasizes skimmability, examples, consistency, freshness, discoverability, and maintainable source organization.
- [Write the Docs: docs as code](https://www.writethedocs.org/guide/docs-as-code/) — integrates documentation with version control, code review, and automated tests.
- [Google developer documentation style guide](https://developers.google.com/style/) — guidance for headings, procedures, voice, links, and accessible images.
- [Google: Write accessible documentation](https://developers.google.com/style/accessibility) — equivalent text for images, lists, and screen-reader-friendly structure.
- [Google: Diagrams, figures, and other images](https://developers.google.com/style/images) — use visuals only when useful, with descriptive alt text and context.
- [Microsoft style guide: step-by-step instructions](https://learn.microsoft.com/en-us/style-guide/procedures-instructions/writing-step-by-step-instructions) — practical rules for scannable, ordered procedures.
