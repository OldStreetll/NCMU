import { Button, Form, Input, InputNumber, Select } from "antd";

export interface ParameterSchema {
  variable: string;
  label: string;
  type: "text" | "paragraph" | "select" | "number";
  required?: boolean;
  options?: string[];
}

export interface DynamicInputFormProps {
  parameters: ParameterSchema[];
  onSubmit: (values: Record<string, unknown>) => void | Promise<void>;
  submitting?: boolean;
}

function renderField(p: ParameterSchema) {
  switch (p.type) {
    case "paragraph":
      return <Input.TextArea rows={4} />;
    case "number":
      return <InputNumber style={{ width: "100%" }} />;
    case "select":
      return (
        <Select
          allowClear
          options={(p.options ?? []).map((o) => ({ label: o, value: o }))}
        />
      );
    case "text":
    default:
      return <Input />;
  }
}

export function DynamicInputForm({
  parameters,
  onSubmit,
  submitting,
}: DynamicInputFormProps) {
  const [form] = Form.useForm();

  const handleFinish = async (values: Record<string, unknown>) => {
    await onSubmit(values);
  };

  return (
    <Form
      form={form}
      layout="vertical"
      onFinish={handleFinish}
      autoComplete="off"
    >
      {parameters.map((p) => (
        <Form.Item
          key={p.variable}
          name={p.variable}
          label={p.label}
          rules={
            p.required ? [{ required: true, message: `${p.label}必填` }] : undefined
          }
        >
          {renderField(p)}
        </Form.Item>
      ))}
      <Form.Item>
        <Button
          type="primary"
          htmlType="submit"
          loading={submitting}
          disabled={submitting}
        >
          提交
        </Button>
      </Form.Item>
    </Form>
  );
}
