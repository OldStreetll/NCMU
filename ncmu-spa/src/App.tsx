import { Suspense } from "react";
import { Spin } from "antd";
import { AppRoutes } from "@/routes";

export default function App() {
  return (
    <Suspense
      fallback={
        <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
          <Spin />
        </div>
      }
    >
      <AppRoutes />
    </Suspense>
  );
}
