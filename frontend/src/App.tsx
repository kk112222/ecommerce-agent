import { useState } from 'react';
import ChatPage from './ChatPage';
import LoginPage from './LoginPage';
import { isLoggedIn } from './auth';

function App() {
  const [authed, setAuthed] = useState(isLoggedIn());

  if (!authed) {
    return <LoginPage onSuccess={() => setAuthed(true)} />;
  }

  return <ChatPage onLogout={() => setAuthed(false)} />;
}

export default App;
