<script setup lang="ts">
import { cva, type VariantProps } from "class-variance-authority";
import { Primitive, type PrimitiveProps } from "reka-ui";
import type { HTMLAttributes } from "vue";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] border border-transparent text-[13px] font-semibold transition-colors outline-none select-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)] disabled:pointer-events-none disabled:opacity-50 active:opacity-90",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-[var(--primary-hover)]",
        secondary: "bg-secondary text-foreground hover:bg-muted",
        outline:
          "border-[var(--border-strong)] bg-card text-foreground hover:bg-muted",
        ghost:
          "bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
        destructive:
          "bg-[var(--destructive-soft)] text-destructive hover:opacity-85",
      },
      size: {
        sm: "h-8 px-2.5 text-xs",
        default: "h-9 px-3.5",
        lg: "h-11 px-4",
        icon: "size-9 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);
type ButtonVariants = VariantProps<typeof buttonVariants>;
interface Props extends PrimitiveProps {
  variant?: ButtonVariants["variant"];
  size?: ButtonVariants["size"];
  class?: HTMLAttributes["class"];
  loading?: boolean;
  disabled?: boolean;
}
const props = withDefaults(defineProps<Props>(), { as: "button" });
</script>

<template>
  <Primitive
    :as="as"
    :as-child="asChild"
    :class="cn('ui-button', buttonVariants({ variant, size }), props.class)"
    :disabled="disabled || loading"
  >
    <span
      v-if="loading"
      class="size-3 animate-spin rounded-full border-2 border-current border-r-transparent"
      aria-hidden="true"
    ></span>
    <slot />
  </Primitive>
</template>
