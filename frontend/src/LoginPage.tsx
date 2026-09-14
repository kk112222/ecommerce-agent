import { useState } from 'react';
import { Form, Input, Button, Tabs, message } from 'antd';
import { LockOutlined, UserOutlined, IdcardOutlined } from '@ant-design/icons';
import { login, register } from './auth';

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
      height: '100dvh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      // 一点极淡的顶部高光 + 近黑底（不再是橘紫渐变）：登录页和主界面同一套语言
      background: 'radial-gradient(900px 420px at 50% -10%, rgba(255,255,255,0.045) 0%, transparent 62%), var(--bg)',
    }}>
      <div className="login-card">
        <div style={{ textAlign: 'center', marginBottom: 20 }}>
          <div className="login-logo">掌</div>
          <div className="login-name">掌柜</div>
          <div className="login-sub">电商运营 AI 助手 · 内部运营团队</div>
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

        <div className="login-tip">测试账号 zhangsan / admin123</div>
      </div>
    </div>
  );
}
