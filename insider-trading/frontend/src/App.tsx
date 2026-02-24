import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import HomePage from "./pages/HomePage";
import MarketPage from "./pages/MarketPage";
import WalletPage from "./pages/WalletPage";

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/markets/:id" element={<MarketPage />} />
          <Route path="/wallets/:address" element={<WalletPage />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
