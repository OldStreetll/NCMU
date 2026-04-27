import { Dropdown } from "antd";
import type { MenuProps } from "antd";
import type { Session } from "./index";

export type SessionItemProps = {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  onRename: () => void;
  onDelete: () => void;
};

export function SessionItem({
  session,
  selected,
  onSelect,
  onRename,
  onDelete,
}: SessionItemProps) {
  const items: MenuProps["items"] = [
    { key: "rename", label: "重命名" },
    { type: "divider" },
    { key: "delete", label: "删除", danger: true },
  ];

  const onMenuClick: MenuProps["onClick"] = ({ key, domEvent }) => {
    domEvent.stopPropagation();
    if (key === "rename") onRename();
    else if (key === "delete") onDelete();
  };

  const title = session.title ?? "未命名";

  return (
    <Dropdown menu={{ items, onClick: onMenuClick }} trigger={["contextMenu"]}>
      <li
        data-testid={`session-item-${session.id}`}
        data-selected={selected ? "true" : "false"}
        onClick={onSelect}
        style={{
          cursor: "pointer",
          padding: "8px 12px",
          borderLeft: selected ? "3px solid #1677ff" : "3px solid transparent",
          background: selected ? "#e6f4ff" : "transparent",
          userSelect: "none",
        }}
      >
        <div
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 14,
          }}
        >
          {title}
        </div>
      </li>
    </Dropdown>
  );
}

export default SessionItem;
