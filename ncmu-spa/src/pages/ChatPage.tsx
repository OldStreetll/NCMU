// TASK-29 stub — TASK-31 overwrites this file as the ChatWindow container,
// then TASK-32 extends it to wire SessionList. Per PLAN-FIX-3 F1 the stub
// exists so routes.tsx can React.lazy(() => import("@/pages/ChatPage"))
// before TASK-31 lands.
export default function ChatPage() {
  return <div>Chat (TASK-31 will implement)</div>;
}
