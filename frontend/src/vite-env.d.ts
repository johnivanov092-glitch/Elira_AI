/// <reference types="vite/client" />

// Allow importing .jsx/.js components without type declarations
declare module "*.jsx" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component: any;
  export default component;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export const executeStream: any;
}

