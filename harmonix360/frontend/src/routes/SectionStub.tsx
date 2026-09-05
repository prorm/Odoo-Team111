import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface SectionStubProps {
  name: string;
  /** Which PS feature codes this section will implement, e.g. "A1 / B2". */
  features: string;
  /** Which phase fills it in, so an empty screen reads as "not yet" rather
   *  than "broken". */
  phase: string;
}

/**
 * A placeholder for a nav section whose screens are not built yet (Phase 0
 * step 8).
 *
 * It states which PS features the section covers and which phase delivers
 * them, because the alternative — an empty page, or a bare "Coming soon" —
 * is indistinguishable from a route that is silently failing to render.
 *
 * Each of these is replaced by the real screen as its phase lands; this
 * component is expected to have no remaining callers by Phase 7.
 */
export function SectionStub({ name, features, phase }: SectionStubProps) {
  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>{name}</CardTitle>
        <CardDescription>Problem statement: {features}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-slate-400">
          The route and its navigation are wired. The screens land in {phase}.
        </p>
      </CardContent>
    </Card>
  );
}
