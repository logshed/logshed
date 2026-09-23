import React, { createContext, useContext, useState, useCallback } from 'react';

export interface AliasContextValue {
  aliasVersion: number;
  bumpAliasVersion: () => void;
}

export const AliasContext = createContext<AliasContextValue>({
  aliasVersion: 0,
  bumpAliasVersion: () => {},
});

export const AliasProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [aliasVersion, setAliasVersion] = useState<number>(0);

  const bumpAliasVersion = useCallback(() => {
    setAliasVersion((prev) => prev + 1);
  }, []);

  return (
    <AliasContext.Provider value={{ aliasVersion, bumpAliasVersion }}>
      {children}
    </AliasContext.Provider>
  );
};

export const useAlias = (): AliasContextValue => {
  return useContext(AliasContext);
};

export const useAliasContext = (): AliasContextValue => {
  return useContext(AliasContext);
};
