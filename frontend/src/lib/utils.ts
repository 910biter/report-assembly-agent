import type { ClassValue } from "clsx";
import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Shared by copied shadcn-vue primitives; safe even before Tailwind is introduced. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
