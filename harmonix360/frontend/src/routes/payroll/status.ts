import type { BadgeProps } from '@/components/ui/badge';
import type { PayrunStatus } from '@/types/payroll';

/** One mapping from payrun status to badge colour, shared by the list and the
 *  detail screen — two copies would drift, and a "paid" run rendered in two
 *  different colours on two screens reads as two different states. */
export const STATUS_VARIANT: Record<PayrunStatus, NonNullable<BadgeProps['variant']>> = {
  draft: 'secondary',
  computed: 'info',
  validated: 'success',
  paid: 'default',
  cancelled: 'destructive',
};
