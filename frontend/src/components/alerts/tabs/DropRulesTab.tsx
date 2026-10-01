import React from 'react';
import { DropRule } from '../../../types.ts';
import { DropRulesCard } from '../../settings/DropRulesCard.tsx';

export interface DropRulesTabProps {
  onRulesChange?: (rules: DropRule[]) => void;
}

export const DropRulesTab: React.FC<DropRulesTabProps> = ({ onRulesChange }) => {
  return <DropRulesCard onRulesChange={onRulesChange} />;
};

export default DropRulesTab;
