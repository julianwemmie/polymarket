import { BrowserRouter, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import EntityPage from "./pages/EntityPage";
import EntitiesPage from "./pages/EntitiesPage";
import MarketPage from "./pages/MarketPage";
import MarketsPage from "./pages/MarketsPage";
import WalletPage from "./pages/WalletPage";

function App() {
  return (
    <BrowserRouter>
      <Layout>
        <Routes>
          <Route path="/" element={<EntitiesPage />} />
          <Route path="/entities" element={<EntitiesPage />} />
          <Route path="/entities/:id" element={<EntityPage />} />
          <Route path="/markets" element={<MarketsPage />} />
          <Route path="/markets/:id" element={<MarketPage />} />
          <Route path="/wallets/:address" element={<WalletPage />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}

export default App;
