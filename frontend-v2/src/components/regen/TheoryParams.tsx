// Theory regen params card. Field names + value enums mirror the
// backend's RegenParams Pydantic schema 1:1 — see backend/app/schemas/regen.py.

import { ParamLabel, ParamRow, ParamTextarea, SegmentChoice } from './PipelineCard';
import type { TheoryRegenParams } from '../../api/regen';

type Props = {
  value: TheoryRegenParams;
  onChange: (next: TheoryRegenParams) => void;
};

export function TheoryParams({ value, onChange }: Props) {
  const upd = <K extends keyof TheoryRegenParams>(
    k: K,
    v: TheoryRegenParams[K],
  ) => onChange({ ...value, [k]: v });

  return (
    <div>
      <ParamRow>
        <ParamLabel hint="How much the rewrite changes the wording. Light = subtle paraphrase, Heavy = full rewrite.">
          Intensity
        </ParamLabel>
        <SegmentChoice
          options={['light', 'moderate', 'heavy'] as const}
          value={value.intensity}
          onChange={(v) => upd('intensity', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Voice / register of the rewritten prose.">Tone</ParamLabel>
        <SegmentChoice
          options={['academic', 'conversational', 'simplified'] as const}
          value={value.tone}
          onChange={(v) => upd('tone', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether equations stay verbatim or get explained in prose.">
          Equations
        </ParamLabel>
        <SegmentChoice
          options={['preserve', 'explain'] as const}
          value={value.equations_handling}
          onChange={(v) => upd('equations_handling', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether figures stay as-is or get described in text.">
          Diagrams
        </ParamLabel>
        <SegmentChoice
          options={['preserve', 'describe'] as const}
          value={value.diagrams_handling}
          onChange={(v) => upd('diagrams_handling', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Whether to add real-world analogies to make concepts relatable.">
          Analogies
        </ParamLabel>
        <SegmentChoice
          options={['none', 'add_one', 'add_multiple'] as const}
          value={value.analogies}
          onChange={(v) => upd('analogies', v)}
          format={(v) => v.replace('_', ' ')}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Keep paragraph order or allow restructuring.">
          Structure
        </ParamLabel>
        <SegmentChoice
          options={['identical', 'reorganize'] as const}
          value={value.structure}
          onChange={(v) => upd('structure', v)}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Optional. Shape the rewrite for a specific audience.">
          Target audience
        </ParamLabel>
        <input
          type="text"
          value={value.target_audience ?? ''}
          onChange={(e) => upd('target_audience', e.target.value || null)}
          placeholder="e.g. Class 10 CBSE students"
          style={{
            width: '100%',
            height: 38,
            padding: '0 12px',
            border: '1px solid var(--line)',
            borderRadius: 10,
            font: 'inherit',
            fontSize: 13,
          }}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Free-form guidance appended to the prompt. E.g. 'Use Indian context examples'.">
          Custom instructions
        </ParamLabel>
        <ParamTextarea
          value={value.custom_instructions ?? ''}
          onChange={(v) => upd('custom_instructions', v || null)}
          placeholder="Any extra rules or style notes for this regeneration…"
        />
      </ParamRow>
    </div>
  );
}
