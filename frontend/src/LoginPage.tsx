import { useState } from 'react';
import { Card, Form, Input, Button, Tabs, Typography, message, Space } from 'antd';
import { RobotOutlined, LockOutlined, UserOutlined, IdcardOutlined } from '@ant-design/icons';
import { login, register } from './auth';

const { Title, Text } = Typography;

interface LoginPageProps {
  onSuccess: () => void;
}

export default function LoginPage({ onSuccess }: LoginPageProps) {
  const [tab, setTab] = useState('login');
  const [loading, setLoading] = useState(false);

  async function handleLogin(values: { username: string; password: string }) {
    setLoading(true);
    try {
      await login(values.username, values.password);
      message.success('登录成功');
      onSuccess();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function handleRegister(values: { username: string; password: string; name?: string }) {
    setLoading(true);
    try {
      await register(values.username, values.password, values.name || '');
      message.success('注册成功，已自动登录');
      onSuccess();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{
      height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #e6f4ff 0%, #f9f0ff 100%)',
    }}>
      <Card style={{ width: 380, boxShadow: '0 4px 12px rgba(0,0,0,0.1)' }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <RobotOutlined style={{ fontSize: 48, color: '#1677ff' }} />
          <Title level={4} style={{ margin: '12px 0 4px' }}>电商运营 AI Agent</Title>
          <Text type="secondary">运营人员的智能副驾</Text>
        </div>

        <Tabs
          activeKey={tab}
          onChange={setTab}
          centered
          items={[
            {
              key: 'login',
              label: '登录',
              children: (
                <Form layout="vertical" onFinish={handleLogin}>
                  <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                    <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
                  </Form.Item>
                  <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
                    <Input.Password prefix={<LockOutlined />} placeholder="密码" autoComplete="current-password" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" block loading={loading}>
                    登录
                  </Button>
                </Form>
              ),
            },
            {
              key: 'register',
              label: '注册',
              children: (
                <Form layout="vertical" onFinish={handleRegister}>
                  <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
                    <Input prefix={<UserOutlined />} placeholder="用户名" autoComplete="username" />
                  </Form.Item>
                  <Form.Item name="name" rules={[{ required: true, message: '请输入昵称' }]}>
                    <Input prefix={<IdcardOutlined />} placeholder="昵称" />
                  </Form.Item>
                  <Form.Item name="password" rules={[
                    { required: true, message: '请输入密码' },
                    { min: 6, message: '密码至少 6 位' },
                  ]}>
                    <Input.Password prefix={<LockOutlined />} placeholder="密码（至少6位）" autoComplete="new-password" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" block loading={loading}>
                    注册
                  </Button>
                </Form>
              ),
            },
          ]}
        />

        <Space style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            测试账号：zhangsan / admin123
          </Text>
        </Space>
      </Card>
    </div>
  );
}
